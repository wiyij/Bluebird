"""A05：最小可用的转发服务端。

职责只有一条：**收到一整帧，转发给所有在线客户端（含发送者）**。没有任何业务
逻辑——登录、在线列表、seq、ACK 都在后面的步骤里加。现在唯一要证明的是：
字节流边界处理是对的，而且数据真的能从 A 机流到 B 机。

    py -3.13 -m server.server                       # 监听 0.0.0.0:8888
    py -3.13 -m server.server --port 9000 --log-level debug   # 换端口、看详细日志

验收：
    netstat -ano | findstr 8888      → 0.0.0.0:8888 LISTENING
    两个终端各跑一个 client，A 输入一行，B 的终端出现同一行
    RST / 半包 / 坏 magic 都不会让进程崩
"""

from __future__ import annotations

import asyncio
import logging

from shared.config import Config, parse_args
from shared.console import setup_console
from shared.protocol import HEADER_SIZE, MsgType, ProtocolError, encode_frame, read_frame

log = logging.getLogger("server")


class EchoServer:
    """最小中继：一帧进来，一帧出去，发给所有在线连接。

    名字还叫 EchoServer，是因为对发送者而言确实是「回显」；A12 会把
    去重、序号、ACK 补上，那时才改名 ChatServer 并拆出 router。
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

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

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in list(self._clients):
            client.close()
        self._clients.clear()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # ---------------------------------------------------------------- 单连接

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        log.info("client connected: %s（当前 %d 人）", peer, len(self._clients))
        try:
            while True:
                # 注意：max_frame 是整帧上限，报文体上限要减掉 8 字节首部
                msg_type, body = await read_frame(reader, self.config.max_frame - HEADER_SIZE)
                log.debug("recv type=0x%02X body=%s", msg_type, body)

                await self._broadcast(encode_frame(msg_type, body))

        except asyncio.IncompleteReadError:
            log.info("client closed: %s", peer)
        except (ConnectionResetError, ConnectionAbortedError):
            log.warning("connection reset: %s", peer)
        except ProtocolError as exc:
            log.warning("protocol error from %s: %s", peer, exc)
            await self._reply_error(writer, exc)
        except asyncio.CancelledError:
            raise
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            log.info("connection closed: %s（当前 %d 人）", peer, len(self._clients))

    async def _broadcast(self, frame: bytes) -> None:
        """写给所有在线连接。写失败的连接就此摘掉，不影响别人。

        A32 会换成「每连接一个写队列」；现在先用最直白的写法，
        把这个阶段的注意力留在「帧对不对」上。
        """
        for client in list(self._clients):
            try:
                client.write(frame)
                await client.drain()
            except (ConnectionError, OSError):
                self._clients.discard(client)
                client.close()

    @staticmethod
    async def _reply_error(writer: asyncio.StreamWriter, exc: ProtocolError) -> None:
        """code 为空表示不该回包（坏 magic、超长帧），直接断。"""
        if exc.code is None:
            return
        try:
            writer.write(encode_frame(
                MsgType.ERROR,
                {"code": int(exc.code), "message": exc.detail or exc.reason},
            ))
            await writer.drain()
        except (ConnectionError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    setup_console()
    config = parse_args(argv, prog="bluebird-server")
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("echo server starting: %s", config.describe())
    try:
        asyncio.run(EchoServer(config).serve_forever())
    except KeyboardInterrupt:
        log.info("interrupted, bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
