"""命令行客户端 v2：登录、群聊、私聊、改色、心跳。

    py -3.13 -m client.cli --name alice                 # 连 127.0.0.1:8888
    py -3.13 -m client.cli --host 192.168.1.10 --name bob --color green

进来先走登录（昵称 + 可选颜色），登录成功后才开始收发。支持的命令：

    /w <昵称> <内容>    私聊，例如 /w bob 在吗
    /color <颜色>       改颜色：black / green / blue / red
    /who                看当前在线
    /quit               退出（等价于 Ctrl+C）

四个异步陷阱必须避开（前两个是 2026-10-24 复审抓到并修掉的）：

1. ``input()`` 是阻塞的，直接调会钉死事件循环 → 放进**守护线程**，用
   ``call_soon_threadsafe`` 把行送回事件循环。不能用 ``asyncio.to_thread``：
   任务被 cancel 后线程仍卡在 ``input()`` 里，而 ``asyncio.run`` 收尾时会等线程池，
   于是「服务端断了，客户端却退不出去，要敲一下回车」。
2. 收发不能串行 → 三个 task 并行（收、发、心跳），谁先结束就取消另外两个。
3. 网络异常必须捕获：``ConnectionResetError`` 不是 ``IncompleteReadError``，
   漏掉就会把一行 Python 异常 repr 甩到用户脸上。
4. 写也要有超时：对端不读时 ``drain()`` 会永远挂着，心跳就再也发不出去。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from shared.config import (HEARTBEAT_INTERVAL, IDLE_TIMEOUT, SEND_TIMEOUT, Config,
                           build_parser)
from shared.console import setup_console
from shared.protocol import (COLORS, DEFAULT_COLOR, HEADER_SIZE, MsgType, ProtocolError,
                             encode_frame, read_frame)

CONNECT_TIMEOUT = 5.0
LOGIN_TIMEOUT = 5.0
QUIT_COMMANDS = {"/quit", "/exit", "/q"}


def _type_name(msg_type: int) -> str:
    try:
        return MsgType(msg_type).name
    except ValueError:
        return f"0x{msg_type:02X}"


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


class ChatClient:
    def __init__(self, config: Config, name: str, color: str = DEFAULT_COLOR) -> None:
        self.config = config
        self.name = name
        self.color = color
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.online: dict[str, dict] = {}
        self._seq = 0
        self._closed = False
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        self._last_rx = time.monotonic()
        # 客户端也遵守同一份帧上限；服务端配了 --max-frame 时两边一致
        self._max_body = max(1, config.max_frame - HEADER_SIZE)

    # ---------------------------------------------------------------- 入口

    async def run(self) -> int:
        target = f"{self.config.host}:{self.config.port}"
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"连接超时：{target} 在 {CONNECT_TIMEOUT:.0f} 秒内没有响应")
            print("  依次检查：服务端是否启动 / 是否监听 0.0.0.0 / 防火墙 / 两台机器是否同网段")
            return 2
        except ConnectionRefusedError:
            print(f"连接被拒绝：{target} 上没有进程监听（服务端没启动？）")
            return 2
        except OSError as exc:
            print(f"连接失败：{exc}")
            return 2

        code = await self._login()
        if code != 0:
            await self._close()
            return 2

        print(f"已进入聊天室：{self.name}（颜色 {self.color}）　{target}")
        print("直接输入 = 群聊；/w 昵称 内容 = 私聊；/color 颜色；/who；/quit")
        self._start_stdin_pump()
        try:
            await self._run_loops()
        finally:
            await self._close()
        return 0

    async def _login(self) -> int:
        """发 LOGIN_REQ 并等 LOGIN_RESP；返回错误码（0 表示成功）。"""
        assert self.reader is not None and self.writer is not None
        self.writer.write(encode_frame(MsgType.LOGIN_REQ, {
            "username": self.name,
            "client_ver": "2.0",
            "color": self.color,
        }))
        await self.writer.drain()

        try:
            msg_type, body = await asyncio.wait_for(
                read_frame(self.reader, self._max_body), LOGIN_TIMEOUT
            )
        except asyncio.TimeoutError:
            print("登录超时：服务端没有回应")
            return 2
        except asyncio.IncompleteReadError:
            print("登录失败：服务端关闭了连接")
            return 2
        except (ConnectionError, OSError) as exc:
            print(f"登录失败：网络中断（{exc.__class__.__name__}）")
            return 2

        if msg_type != MsgType.LOGIN_RESP:
            print(f"登录失败：期望 LOGIN_RESP，收到 {_type_name(msg_type)}")
            return 2

        code = int(body.get("code", -1))
        if code != 0:
            print(f"登录失败（{code}）：{body.get('message', '未知原因')}")
            return 2

        self.name = body.get("nickname", self.name)
        self.color = body.get("color", self.color)
        self._last_rx = time.monotonic()
        return 0

    def _start_stdin_pump(self) -> None:
        """把阻塞的 input() 挪进守护线程，避免它拖住进程退出。"""
        loop = asyncio.get_running_loop()
        queue = self._lines

        def pump() -> None:
            while True:
                try:
                    line = input()
                except (EOFError, KeyboardInterrupt):
                    line = None
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
                except RuntimeError:      # 事件循环已关闭，线程自行收工
                    return
                if line is None:          # 输入结束，发一个哨兵让发送循环收尾
                    return

        threading.Thread(target=pump, name="stdin", daemon=True).start()

    async def _run_loops(self) -> None:
        tasks = {
            asyncio.create_task(self._recv_loop(), name="recv"),
            asyncio.create_task(self._send_loop(), name="send"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                print(f"[异常] 网络任务意外结束：{exc.__class__.__name__}: {exc}")

    async def _close(self) -> None:
        self._closed = True
        if self.writer is None:
            return
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    def _abort(self) -> None:
        """立即断：本地已经判定这条连接死了，不要再等 TCP 慢慢超时。"""
        self._closed = True
        transport = getattr(self.writer, "transport", None) if self.writer else None
        if transport is not None:
            transport.abort()

    # ---------------------------------------------------------------- 收

    async def _recv_loop(self) -> None:
        assert self.reader is not None
        while True:
            try:
                msg_type, body = await read_frame(self.reader, self._max_body)
            except asyncio.IncompleteReadError:
                print("\n[连接已断开] 服务端关闭了连接")
                return
            except ProtocolError as exc:
                print(f"\n[协议错误] {exc.detail or exc.reason}")
                return
            except (ConnectionError, OSError) as exc:
                # 断网、服务端被杀、路由不可达都从这里出来（典型是 ConnectionResetError）
                print(f"\n[连接已断开] 网络中断（{exc.__class__.__name__}）")
                return
            self._last_rx = time.monotonic()
            self._render(msg_type, body)

    def _render(self, msg_type: int, body: dict) -> None:
        ts = _stamp()
        if msg_type == MsgType.CHAT_GROUP:
            print(f"{ts}  [{body.get('from')}：{body.get('text')}]")
        elif msg_type == MsgType.CHAT_PRIVATE:
            sender, to = body.get("from"), body.get("to")
            print(f"{ts}  [私聊 {sender}→{to}：{body.get('text')}]")
        elif msg_type == MsgType.CHAT_SYSTEM:
            print(f"{ts}  [system：{body.get('text')}]")
        elif msg_type == MsgType.USER_LIST:
            self.online = {u["nickname"]: u for u in body.get("users", [])}
            names = "、".join(f"{n}({u.get('color')})" for n, u in self.online.items())
            print(f"{ts}  [system：在线 {body.get('count', len(self.online))} 人] {names}")
        elif msg_type == MsgType.USER_JOIN:
            # A14：名单只负责「谁在线」，界面提示统一由 CHAT_SYSTEM 来
            self.online[body["nickname"]] = body
        elif msg_type == MsgType.USER_LEAVE:
            self.online.pop(body.get("nickname", ""), None)
        elif msg_type == MsgType.USER_COLOR:
            if body.get("nickname") in self.online:
                self.online[body["nickname"]]["color"] = body.get("color")
            print(f"{ts}  [system：{body.get('nickname')} 的颜色改为 {body.get('color')}]")
        elif msg_type == MsgType.ERROR:
            print(f"{ts}  [错误 {body.get('code')}] {body.get('message')}")
        elif msg_type == MsgType.PONG:
            pass
        else:
            print(f"{ts}  ← {_type_name(msg_type)} {json.dumps(body, ensure_ascii=False)}")

    # ---------------------------------------------------------------- 发

    async def _send_loop(self) -> None:
        while not self._closed:
            line = await self._lines.get()
            if line is None:
                print("\n[输入结束]")
                return
            if self._closed:
                return

            text = line.strip()
            if not text:
                continue
            if text in QUIT_COMMANDS:
                await self._send_logout()
                return

            if text.startswith("/w "):
                await self._send_private(text[3:])
            elif text.startswith("/color "):
                await self._send_color(text[7:].strip())
            elif text == "/who":
                names = "、".join(f"{n}({u.get('color')})" for n, u in self.online.items())
                print(f"[在线 {len(self.online)} 人] {names or '（无）'}")
            elif text == "/help":
                print("直接输入=群聊；/w 昵称 内容=私聊；/color 颜色；/who；/quit")
            else:
                await self._send(MsgType.CHAT_GROUP, {
                    "msg_id": self._next_msg_id(),
                    "text": text,
                })

    async def _send_private(self, rest: str) -> None:
        parts = rest.split(maxsplit=1)
        if len(parts) != 2:
            print("用法：/w 昵称 内容")
            return
        to, content = parts
        await self._send(MsgType.CHAT_PRIVATE, {
            "msg_id": self._next_msg_id(),
            "to": to,
            "text": content,
        })

    async def _send_color(self, color: str) -> None:
        if color not in COLORS:
            print("颜色只能是：" + " / ".join(COLORS))
            return
        await self._send(MsgType.SET_COLOR, {"color": color})

    async def _send_logout(self) -> None:
        try:
            await self._send(MsgType.LOGOUT, {})
        except (ConnectionError, OSError):
            pass

    async def _send(self, msg_type: int, body: dict | None = None) -> None:
        assert self.writer is not None
        try:
            self.writer.write(encode_frame(msg_type, body))
            await asyncio.wait_for(self.writer.drain(), SEND_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"[发送失败] 对端 {SEND_TIMEOUT:.0f} 秒没有收数据，判定断线")
            self._abort()
        except ValueError as exc:
            print(f"[发送失败] {exc}")
        except (ConnectionError, OSError):
            print("[发送失败] 连接已断开")
            self._closed = True

    # ---------------------------------------------------------------- 心跳

    async def _heartbeat_loop(self) -> None:
        """A17：每 15 s 一个 PING；45 s 收不到任何帧就判死（= 连丢 3 个 PONG）。

        判据不是「有没有收到 PONG」而是「有没有收到任何帧」：服务端在忙时
        PONG 可能排在别的消息后面，只盯 PONG 会误判。
        """
        while not self._closed:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._closed:
                return
            if time.monotonic() - self._last_rx > IDLE_TIMEOUT:
                print(f"\n[连接超时] {IDLE_TIMEOUT:.0f} 秒没有收到服务端任何数据，判定断线")
                self._abort()
                return
            await self._send(MsgType.PING)

    def _next_msg_id(self) -> str:
        self._seq += 1
        return f"{self.name}-{self._seq:06d}"


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = build_parser("bluebird-cli", default_host="127.0.0.1")
    parser.add_argument("--name", default="guest", help="昵称，1—16 个字符")
    parser.add_argument("--color", default=DEFAULT_COLOR, choices=COLORS, help="字体颜色")
    ns = parser.parse_args(argv)

    name = ns.name.strip()
    if not 1 <= len(name) <= 16:
        print("昵称需 1—16 个字符")
        return 2

    config = Config(host=ns.host, port=ns.port, max_frame=ns.max_frame, log_level=ns.log_level)
    try:
        return asyncio.run(ChatClient(config, name, ns.color).run())
    except KeyboardInterrupt:
        print("\n[已退出]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
