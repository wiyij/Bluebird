"""A06：命令行客户端 —— 连上服务端、发一帧、把收到的帧打印出来。

现在还没有登录（A10 才做），所以每条输入直接封成 ``CHAT_GROUP`` 发出去；
服务端此刻是 echo，会把它原样送回来，你在屏幕上看到的就是自己发的那条。

    py -3.13 -m client.cli                          # 连 127.0.0.1:8888，昵称 guest
    py -3.13 -m client.cli --host 192.168.1.10 --name alice

输入内容回车发送，``/quit`` 退出，Ctrl+C 也可以。

两个异步陷阱在这里必须避开：
1. ``input()`` 是阻塞的，直接调会把事件循环钉死 → 用 ``asyncio.to_thread`` 丢到线程；
2. 收发不能串行 → 两个 task 并行，谁先结束就取消另一个。
"""

from __future__ import annotations

import asyncio
import json
import time

from shared.config import Config, build_parser
from shared.console import setup_console
from shared.protocol import MsgType, ProtocolError, encode_frame, read_frame

CONNECT_TIMEOUT = 5.0
QUIT_COMMANDS = {"/quit", "/exit", "/q"}


def _type_name(msg_type: int) -> str:
    try:
        return MsgType(msg_type).name
    except ValueError:
        return f"0x{msg_type:02X}"


def format_incoming(msg_type: int, body: dict) -> str:
    """一条收到的消息 → 一行终端输出。"""
    stamp = time.strftime("%H:%M:%S")
    payload = json.dumps(body, ensure_ascii=False)
    return f"{stamp}  ←  {_type_name(msg_type):<12} {payload}"


class ChatClient:
    def __init__(self, config: Config, name: str) -> None:
        self.config = config
        self.name = name
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
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
            print("  依次检查：服务端是否启动 / 是否监听 0.0.0.0 / 防火墙是否放行 / 两台机器是否同网段")
            return 2
        except ConnectionRefusedError:
            print(f"连接被拒绝：{target} 上没有进程监听")
            print("  多半是服务端没启动，或它只监听了 127.0.0.1")
            return 2
        except OSError as exc:
            print(f"连接失败：{exc}")
            return 2

        print(f"已连接 {target}，昵称 {self.name}（{_type_name(MsgType.CHAT_GROUP)} 模式）")
        print("输入内容回车发送，/quit 退出")
        try:
            await self._run_loops()
        finally:
            await self._close()
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

    # ---------------------------------------------------------------- 收发

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
            print(format_incoming(msg_type, body))

    async def _send_loop(self) -> None:
        assert self.writer is not None
        while not self._closed:
            try:
                line = await asyncio.to_thread(input, "")
            except EOFError:            # Ctrl+Z / 管道读完
                print("\n[输入结束]")
                return
            if self._closed:
                return

            text = line.strip()
            if not text:
                continue
            if text in QUIT_COMMANDS:
                return

            self._seq += 1
            try:
                frame = encode_frame(
                    MsgType.CHAT_GROUP,
                    {"msg_id": f"{self.name}-{self._seq:06d}", "text": text},
                )
            except ValueError as exc:
                # 终端编码异常（如管道送进来孤立代理字符）不该让客户端退出
                print(f"[发送失败] {exc}")
                continue

            self.writer.write(frame)
            await self.writer.drain()


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = build_parser("bluebird-cli", default_host="127.0.0.1")
    parser.add_argument("--name", default="guest", help="昵称，1—16 个字符")
    ns = parser.parse_args(argv)

    name = ns.name.strip()
    if not 1 <= len(name) <= 16:
        print("昵称需 1—16 个字符")
        return 2

    config = Config(host=ns.host, port=ns.port, max_frame=ns.max_frame, log_level=ns.log_level)
    try:
        return asyncio.run(ChatClient(config, name).run())
    except KeyboardInterrupt:
        print("\n[已退出]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
