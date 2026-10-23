"""命令行客户端 v2：登录、群聊、私聊、改色。

    py -3.13 -m client.cli --name alice                 # 连 127.0.0.1:8888
    py -3.13 -m client.cli --host 192.168.1.10 --name bob --color green

进来先走登录（昵称 + 可选颜色），登录成功后才开始收发。支持的命令：

    /w <昵称> <内容>    私聊，例如 /w bob 在吗
    /color <颜色>       改颜色：black / green / blue / red
    /who                看当前在线
    /quit               退出（等价于 Ctrl+C）

两个异步陷阱必须避开：
1. ``input()`` 是阻塞的，直接调会钉死事件循环 → 用 ``asyncio.to_thread``；
2. 收发不能串行 → 两个 task 并行，谁先结束就取消另一个。
"""

from __future__ import annotations

import asyncio
import json
import time

from shared.config import Config, build_parser
from shared.console import setup_console
from shared.errors import ErrorCode
from shared.protocol import (COLORS, DEFAULT_COLOR, MsgType, ProtocolError,
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
            msg_type, body = await asyncio.wait_for(read_frame(self.reader), LOGIN_TIMEOUT)
        except asyncio.TimeoutError:
            print("登录超时：服务端没有回应")
            return 2
        except asyncio.IncompleteReadError:
            print("登录失败：服务端关闭了连接")
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
        return 0

    async def _run_loops(self) -> None:
        tasks = {
            asyncio.create_task(self._recv_loop(), name="recv"),
            asyncio.create_task(self._send_loop(), name="send"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                print(f"[异常] {exc!r}")

    async def _close(self) -> None:
        self._closed = True
        if self.writer is None:
            return
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    # ---------------------------------------------------------------- 收

    async def _recv_loop(self) -> None:
        assert self.reader is not None
        while True:
            try:
                msg_type, body = await read_frame(self.reader)
            except asyncio.IncompleteReadError:
                print("[连接已断开]")
                return
            except ProtocolError as exc:
                print(f"[协议错误] {exc}")
                return
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
            self.online[body["nickname"]] = body
            print(f"{ts}  [system：{body['nickname']} 进入聊天室]")
        elif msg_type == MsgType.USER_LEAVE:
            self.online.pop(body.get("nickname", ""), None)
            reason = {"logout": "退出了", "disconnect": "离线了"}.get(body.get("reason"), "离线了")
            print(f"{ts}  [system：{body.get('nickname')} {reason}]")
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
        assert self.writer is not None
        while not self._closed:
            try:
                line = await asyncio.to_thread(input, "")
            except EOFError:
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

    async def _send(self, msg_type: int, body: dict) -> None:
        assert self.writer is not None
        try:
            self.writer.write(encode_frame(msg_type, body))
            await self.writer.drain()
        except ValueError as exc:
            print(f"[发送失败] {exc}")
        except (ConnectionError, OSError):
            print("[发送失败] 连接已断开")
            self._closed = True

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
