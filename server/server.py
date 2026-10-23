"""服务端：监听、读帧、分发、广播。

A05 的 echo 已经升级成「读一帧 → 查路由器 → 交给对应处理函数」：
- ``CHAT_GROUP`` → 广播给所有在线连接（含发送者，用于回显）
- ``PING`` → 回 ``PONG{server_time}``
- 其他类型 → ``ERROR(2004)``，**连接保持**

登录、在线列表、seq、ACK 还没有——A10 之后逐条接上。

    py -3.13 -m server.server
    py -3.13 -m server.server --port 9000 --log-level debug

验收：
    netstat -ano | findstr 8888      → 0.0.0.0:8888 LISTENING
    两个终端各跑一个 client，A 输入一行，B 的终端出现同一行
    RST / 半包 / 坏 magic / 未知类型 都不会让进程崩
"""

from __future__ import annotations

import asyncio
import logging
import time

from server.connection import Client
from server.router import Router
from shared.config import Config, parse_args
from shared.console import setup_console
from shared.protocol import HEADER_SIZE, MsgType, ProtocolError, encode_frame, read_frame

log = logging.getLogger("server")


class ChatServer:
    """所有在线连接的集合 + 一张消息类型分发表。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[Client] = set()
        self.router = Router()
        self._register_handlers()

    # ---------------------------------------------------------------- 业务处理

    def _register_handlers(self) -> None:
        """新增一种消息类型，只需要在这里加一个函数。"""

        @self.router.handler(MsgType.CHAT_GROUP)
        async def on_group(client: Client, body: dict) -> None:
            log.debug("group message from %s: %s", client.label, body.get("text"))
            await self.broadcast(encode_frame(MsgType.CHAT_GROUP, body))

        @self.router.handler(MsgType.PING)
        async def on_ping(client: Client, body: dict) -> None:
            await client.send(MsgType.PONG, {"server_time": int(time.time())})

    async def broadcast(self, frame: bytes) -> None:
        """写给所有在线连接。写失败的连接就此摘掉，不影响别人。"""
        for client in list(self._clients):
            if not client.alive:
                self._clients.discard(client)
                continue
            try:
                await client.send_frame(frame)
            except (ConnectionError, OSError):
                log.warning("write failed, dropping %s", client.label)
                self._clients.discard(client)
                await client.close()

    # ---------------------------------------------------------------- 生命周期

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(
            self._handle, self.config.host, self.config.port
        )
        for sock in self._server.sockets:
            log.info("listening on %s:%s", *sock.getsockname()[:2])
        return self._server

    @property
    def port(self) -> int:
        """实际绑定的端口（测试用 0 端口时由系统分配）。"""
        if self._server is None:
            raise RuntimeError("服务端还没启动")
        return self._server.sockets[0].getsockname()[1]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in list(self._clients):
            await client.close()
        self._clients.clear()

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # ---------------------------------------------------------------- 单连接

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = Client(reader=reader, writer=writer, peer=writer.get_extra_info("peername"))
        self._clients.add(client)
        log.info("client connected: %s（当前 %d 人）", client.label, len(self._clients))
        try:
            while True:
                # max_frame 是整帧上限，报文体上限要减掉 8 字节首部
                msg_type, body = await read_frame(reader, self.config.max_frame - HEADER_SIZE)
                log.debug("recv type=0x%02X body=%s", msg_type, body)
                await self.router.dispatch(client, msg_type, body)

        except asyncio.IncompleteReadError:
            log.info("client closed: %s", client.label)
        except (ConnectionResetError, ConnectionAbortedError):
            log.warning("connection reset: %s", client.label)
        except ProtocolError as exc:
            log.warning("protocol error from %s: %s", client.label, exc)
            if exc.code is not None:
                try:
                    await client.send_error(exc.code, exc.detail or exc.reason)
                except (ConnectionError, OSError):
                    pass
        except asyncio.CancelledError:
            raise
        finally:
            self._clients.discard(client)
            await client.close()
            log.info("connection closed: %s（当前 %d 人）", client.label, len(self._clients))


def main(argv: list[str] | None = None) -> int:
    setup_console()
    config = parse_args(argv, prog="bluebird-server")
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("chat server starting: %s", config.describe())
    try:
        asyncio.run(ChatServer(config).serve_forever())
    except KeyboardInterrupt:
        log.info("interrupted, bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
