"""服务端 v2：昵称登录、群聊、一对一私聊、颜色广播、上下线通知。

协议见 docs/protocol.md（v2）。这一版**没有账号系统**：昵称只在本次会话有效，
在线期间唯一，服务端用一张内存表维护，进程重启即清空。

    登录   LOGIN_REQ  → 校验格式与重名 → LOGIN_RESP(ok) → USER_LIST → 广播 USER_JOIN
    群聊   CHAT_GROUP → 分配 seq → 广播给所有人（含发送者回显）
    私聊   CHAT_PRIVATE → 按 to 找连接 → 单投给双方；找不到回 3001，不暂存
    改色   SET_COLOR → 校验四色 → 广播 USER_COLOR
    心跳   PING → PONG

    py -3.13 -m server.server
    py -3.13 -m server.server --port 9000 --log-level debug
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from server.connection import Client
from server.router import Router
from shared.config import Config, parse_args
from shared.console import setup_console
from shared.errors import ErrorCode
from shared.protocol import (COLORS, DEFAULT_COLOR, HEADER_SIZE, MAX_TEXT_BYTES,
                             MsgType, ProtocolError, encode_frame, is_valid_color, read_frame)

log = logging.getLogger("server")

#: 昵称：1—16 字符，中英文、数字、下划线
NICKNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{1,16}$")


class ChatServer:
    """在线昵称表 + 消息路由器。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[str, Client] = {}      # nickname -> Client
        self._seq = 0
        self.router = Router()
        self._register_handlers()

    # ---------------------------------------------------------------- 业务

    def _register_handlers(self) -> None:
        """新增一种消息类型，只需要在这里加一个函数。"""
        r = self.router
        r.register(MsgType.LOGIN_REQ, self._on_login)
        r.register(MsgType.LOGOUT, self._on_logout)
        r.register(MsgType.CHAT_GROUP, self._on_group)
        r.register(MsgType.CHAT_PRIVATE, self._on_private)
        r.register(MsgType.SET_COLOR, self._on_set_color)
        r.register(MsgType.PING, self._on_ping)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _on_login(self, client: Client, body: dict) -> None:
        if client.authed:
            await client.send_error(ErrorCode.BAD_FRAME, "已经登录过了")
            return

        nickname = str(body.get("username", "")).strip()
        if not NICKNAME_RE.match(nickname):
            await client.send(MsgType.LOGIN_RESP, {
                "code": int(ErrorCode.BAD_NICKNAME),
                "message": "昵称需 1—16 个字符，仅支持中英文、数字与下划线",
            })
            client.state = "closing"
            return

        if nickname in self._clients:
            await client.send(MsgType.LOGIN_RESP, {
                "code": int(ErrorCode.NICK_TAKEN),
                "message": "该昵称已有人使用，请换一个",
            })
            client.state = "closing"
            return

        color = body.get("color", DEFAULT_COLOR)
        if not is_valid_color(color):
            color = DEFAULT_COLOR              # 非法颜色按默认黑处理，不算错误

        client.nickname = nickname
        client.color = color
        client.since = int(time.time())
        client.state = "authed"
        self._clients[nickname] = client

        await client.send(MsgType.LOGIN_RESP, {
            "code": 0,
            "nickname": nickname,
            "color": color,
            "server_time": int(time.time()),
        })
        await client.send(MsgType.USER_LIST, {
            "users": [c.profile() for c in self._clients.values()],
            "count": len(self._clients),
        })
        await self._broadcast(MsgType.USER_JOIN, client.profile(), exclude=client)
        log.info("login: %s（在线 %d 人）", nickname, len(self._clients))

    async def _on_logout(self, client: Client, body: dict) -> None:
        client.leave_reason = "logout"
        client.state = "closing"

    async def _on_group(self, client: Client, body: dict) -> None:
        if not await self._require_auth(client):
            return
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            await client.send_error(ErrorCode.BAD_FRAME, "消息不能为空")
            return
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            await client.send_error(ErrorCode.MESSAGE_TOO_LONG)
            return

        payload = {
            "seq": self._next_seq(),
            "from": client.nickname,
            "text": text,
            "server_ts": int(time.time()),
        }
        if "msg_id" in body:
            payload["msg_id"] = body["msg_id"]
        await self._broadcast(MsgType.CHAT_GROUP, payload)

    async def _on_private(self, client: Client, body: dict) -> None:
        if not await self._require_auth(client):
            return
        to = str(body.get("to", "")).strip()
        text = body.get("text")
        if not to or not isinstance(text, str) or not text.strip():
            await client.send_error(ErrorCode.BAD_FRAME, "私聊需要 to 与 text")
            return
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            await client.send_error(ErrorCode.MESSAGE_TOO_LONG)
            return

        target = self._clients.get(to)
        if target is None:
            # 没有离线队列：对方不在线就不投递，也不暂存
            await client.send_error(ErrorCode.USER_OFFLINE, f"{to} 不在线")
            return

        payload = {
            "seq": self._next_seq(),
            "from": client.nickname,
            "to": to,
            "text": text,
            "server_ts": int(time.time()),
        }
        if "msg_id" in body:
            payload["msg_id"] = body["msg_id"]

        await target.send(MsgType.CHAT_PRIVATE, payload)
        if target is not client:
            await client.send(MsgType.CHAT_PRIVATE, payload)

    async def _on_set_color(self, client: Client, body: dict) -> None:
        if not await self._require_auth(client):
            return
        color = body.get("color")
        if not is_valid_color(color):
            await client.send_error(
                ErrorCode.BAD_FRAME,
                "颜色只能是 " + " / ".join(COLORS),
            )
            return
        client.color = color
        await self._broadcast(MsgType.USER_COLOR, {
            "nickname": client.nickname,
            "color": color,
        })

    async def _on_ping(self, client: Client, body: dict) -> None:
        await client.send(MsgType.PONG, {"server_time": int(time.time())})

    async def _require_auth(self, client: Client) -> bool:
        if client.authed:
            return True
        await client.send_error(ErrorCode.NOT_LOGGED_IN)
        client.state = "closing"
        return False

    # ---------------------------------------------------------------- 广播

    async def _broadcast(self, msg_type: MsgType, body: dict,
                         exclude: Client | None = None) -> None:
        frame = encode_frame(msg_type, body)
        for client in list(self._clients.values()):
            if client is exclude or not client.alive:
                continue
            try:
                await client.send_frame(frame)
            except (ConnectionError, OSError):
                log.warning("write failed, dropping %s", client.label)

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
        for client in list(self._clients.values()):
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
        log.info("client connected: %s", client.label)
        try:
            while True:
                # max_frame 是整帧上限，报文体上限要减掉 8 字节首部
                msg_type, body = await read_frame(reader, self.config.max_frame - HEADER_SIZE)
                log.debug("recv type=0x%02X body=%s", msg_type, body)
                await self.router.dispatch(client, msg_type, body)
                if client.state == "closing":
                    break

        except asyncio.IncompleteReadError:
            log.info("client closed: %s", client.label)
        except (ConnectionResetError, ConnectionAbortedError):
            log.warning("connection reset: %s", client.label)
            client.leave_reason = "disconnect"
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
            await self._forget(client)
            log.info("connection closed: %s（在线 %d 人）", client.label, len(self._clients))

    async def _forget(self, client: Client) -> None:
        """把连接从在线表里摘掉，并通知其他人。"""
        nickname = client.nickname
        if nickname and self._clients.get(nickname) is client:
            del self._clients[nickname]
            await self._broadcast(MsgType.USER_LEAVE, {
                "nickname": nickname,
                "reason": client.leave_reason,
            })
        await client.close()


def main(argv: list[str] | None = None) -> int:
    setup_console()
    config = parse_args(argv, prog="bluebird-server")
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("chat server v2 starting: %s", config.describe())
    try:
        asyncio.run(ChatServer(config).serve_forever())
    except KeyboardInterrupt:
        log.info("interrupted, bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
