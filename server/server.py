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
import contextlib
import logging
import re
import time

from server.connection import Client
from server.heartbeat import expired
from server.router import Router
from shared.config import IDLE_TIMEOUT, SWEEP_INTERVAL, Config, parse_args
from shared.console import setup_console
from shared.errors import ErrorCode, message_for
from shared.protocol import (COLORS, DEFAULT_COLOR, HEADER_SIZE, MAX_TEXT_BYTES,
                             MsgType, ProtocolError, encode_frame, is_valid_color, read_frame)

log = logging.getLogger("server")

#: 昵称：1—16 字符，中英文、数字、下划线
NICKNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{1,16}$")

#: 登录时限（协议第 8 章）：连上 10 s 内不发 LOGIN_REQ 就关掉，防占位连接
LOGIN_TIMEOUT = 10.0

#: 离场原因 → 系统消息里的说法。键必须落在协议第 4.1 章允许的取值内
#: （``logout`` / ``disconnect``）；「超时下线」这种更细的说法放 ``leave_note``，
#: 不要往 ``reason`` 里塞协议没有的值。
LEAVE_TEXT = {
    "logout": "退出了",
    "disconnect": "离线了",
}


class ChatServer:
    """在线昵称表 + 消息路由器。"""

    def __init__(
        self,
        config: Config,
        *,
        login_timeout: float = LOGIN_TIMEOUT,
        idle_timeout: float = IDLE_TIMEOUT,
        sweep_interval: float = SWEEP_INTERVAL,
        exit_when_idle: float = 0.0,
    ) -> None:
        self.config = config
        self.login_timeout = login_timeout
        self.idle_timeout = idle_timeout
        self.sweep_interval = sweep_interval
        #: >0 时：没人连着的时候等这么久，服务端自己退出（桌面快捷方式用）
        self.exit_when_idle = exit_when_idle if exit_when_idle > 0 else config.idle_exit
        self._empty_since: float | None = None
        #: 当前连着的 TCP 连接数（**含刚连上、还没登录完的**）。空房计时看这个，
        #: 不看 `_clients`：从「连上」到「登录成功」之间还有一次握手，那段时间
        #: 用户正在输昵称，不该被当成「没人」。
        self._live = 0
        self._server: asyncio.AbstractServer | None = None
        self._sweeper: asyncio.Task | None = None
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

        # 协议 §0 纪律 1：必填字段缺失或类型不对 → 2003，绝不「补个默认值」。
        # 登录阶段按 §4.2 走 LOGIN_RESP；这是 §5 里 2003「连接保持」的例外，
        # 因为此时还没有会话可保持。
        username = body.get("username")
        client_ver = body.get("client_ver")
        if not isinstance(username, str) or not isinstance(client_ver, str) or not client_ver.strip():
            await self._refuse_login(
                client, ErrorCode.BAD_FRAME,
                "LOGIN_REQ 需要 username 与 client_ver 两个字符串字段",
            )
            return

        nickname = username.strip()
        if not NICKNAME_RE.match(nickname):
            await self._refuse_login(client, ErrorCode.BAD_NICKNAME)
            return
        if nickname in self._clients:
            await self._refuse_login(client, ErrorCode.NICK_TAKEN)
            return

        color = body.get("color", DEFAULT_COLOR)
        if not is_valid_color(color):
            color = DEFAULT_COLOR              # 非法颜色按默认黑处理，不算错误

        client.nickname = nickname
        client.color = color
        client.since = int(time.time())
        client.state = "authed"
        client.touch()                         # 登录这一帧也算「活着」
        self._empty_since = None               # 有人进来，空房计时清零
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
        await self._system(f"{nickname} 进入聊天室", exclude=client)
        log.info("login: %s（在线 %d 人）", nickname, len(self._clients))

    async def _refuse_login(self, client: Client, code: ErrorCode,
                            message: str | None = None) -> None:
        """登录失败统一出口：回 LOGIN_RESP{code≠0} 后关闭（协议 §4.2）。"""
        await client.send(MsgType.LOGIN_RESP, {
            "code": int(code),
            "message": message or message_for(code),
        })
        client.state = "closing"

    async def _on_logout(self, client: Client, body: dict) -> None:
        client.leave_reason = "logout"
        client.state = "closing"

    async def _on_group(self, client: Client, body: dict) -> None:
        if not await self._require_auth(client):
            return
        if not client.limiter.allow():
            log.info("rate limited: %s", client.label)
            await client.send_error(ErrorCode.RATE_LIMITED)
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
        if not client.limiter.allow():
            log.info("rate limited: %s", client.label)
            await client.send_error(ErrorCode.RATE_LIMITED)
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
            except (TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
                # 写不进去（对端不读 / 已断 / 缓冲满）就立刻断掉这条。
                # 只 log 不处理会留下「幽灵用户」：昵称一直被占，本人重连回 1001。
                log.warning("write failed, aborting %s: %s: %s",
                            client.label, exc.__class__.__name__, exc)
                client.abort()

    async def _system(self, text: str, level: str = "info",
                      exclude: Client | None = None) -> None:
        """A14：所有系统提示只从这里发，统一成 CHAT_SYSTEM 一帧。"""
        await self._broadcast(MsgType.CHAT_SYSTEM, {
            "text": text,
            "level": level,
            "ts": int(time.time()),
        }, exclude=exclude)

    # ---------------------------------------------------------------- 生命周期

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(
            self._handle, self.config.host, self.config.port
        )
        for sock in self._server.sockets:
            log.info("listening on %s:%s", *sock.getsockname()[:2])
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="heartbeat-sweep")
        return self._server

    async def _sweep_loop(self) -> None:
        """A17：每 5 s 扫一次，45 s 没收到任何帧就判死并广播下线（协议 §4.6）。

        顺带管「没人连着就收工」：桌面快捷方式起的服务端带 ``--idle-exit``，
        一条连接都没有时等一会儿就自己退出，免得关掉窗口后留下一个看不见的后台
        进程（下次双击还会连到那个跑着旧代码的服务端）。

        计时的依据是**连接数**而不是在线人数：点「进入聊天室」之后、登录成功之前
        已经占了一条连接，用户慢慢输昵称时不该被判成「没人」；反过来，开了登录窗
        却从没点进入就关掉，一条连接都没有，计时从服务端启动那一刻就在走，
        2 分钟后自然退出——这正是复验报告里 P2 指的那个坑。
        """
        while True:
            await asyncio.sleep(self.sweep_interval)
            for client in expired(list(self._clients.values()), timeout=self.idle_timeout):
                log.info("heartbeat timeout: %s（%.0f s 无帧）", client.label, self.idle_timeout)
                client.leave_reason = "disconnect"      # 协议 §4.6 就是这么定的
                client.leave_note = "超时下线"          # 只有系统提示用这个说法
                client.abort()                          # 读循环醒来 → _forget → 广播下线

            if self.exit_when_idle <= 0:
                continue                                # 没开这个开关就永远不退出
            if self._live:
                self._empty_since = None
                continue
            if self._empty_since is None:
                self._empty_since = time.monotonic()
                log.info("没人连着，%.0f s 后自动退出", self.exit_when_idle)
            elif time.monotonic() - self._empty_since >= self.exit_when_idle:
                log.info("空闲超时，服务端退出")
                if self._server is not None:
                    self._server.close()                # serve_forever 随之返回
                return

    @property
    def port(self) -> int:
        """实际绑定的端口（测试用 0 端口时由系统分配）。"""
        if self._server is None:
            raise RuntimeError("服务端还没启动")
        return self._server.sockets[0].getsockname()[1]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def serving(self) -> bool:
        """还在接连接吗（测试「空房自动退出」用）。"""
        return self._server is not None and self._server.is_serving()

    @property
    def live_connections(self) -> int:
        """当前连着的 TCP 连接数（含未登录的）。"""
        return self._live

    async def close(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None
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
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            # Server.serve_forever() 在服务端被 close() 时就是抛这个异常表示「收工了」，
            # 空房自退走的就是这条路；不接住的话退出时会打一段看不懂的 traceback。
            log.info("服务端已关闭")

    # ---------------------------------------------------------------- 单连接

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = Client(reader=reader, writer=writer, peer=writer.get_extra_info("peername"))
        log.info("client connected: %s", client.label)
        self._live += 1                        # 空房计时看这个：连上就算「有人」
        # max_frame 是整帧上限，报文体上限要减掉 8 字节首部
        body_limit = self.config.max_frame - HEADER_SIZE
        tolerated_bad_frame = False
        try:
            # ---- 登录阶段：10 s 内必须发来 LOGIN_REQ（协议 §1、§6、§8）----
            try:
                msg_type, body = await asyncio.wait_for(
                    read_frame(reader, body_limit), timeout=self.login_timeout
                )
            except asyncio.TimeoutError:
                log.info("login timeout: %s", client.label)
                return                      # finally 里关连接
            client.touch()
            if msg_type != MsgType.LOGIN_REQ:
                # 未登录就发业务帧/心跳 → 1006 并断开
                log.warning("frame 0x%02X before login from %s", msg_type, client.label)
                await client.send_error(ErrorCode.NOT_LOGGED_IN)
                return
            await self.router.dispatch(client, msg_type, body)

            while client.state != "closing":
                try:
                    msg_type, body = await read_frame(reader, body_limit)
                except ProtocolError as exc:
                    # 协议 §2、§5：2003 容错一次（帧边界没乱，可以接着读）；
                    # 坏 magic / 超长帧 / 版本不符一律断开，交给外层统一收尾。
                    if exc.code != ErrorCode.BAD_FRAME or tolerated_bad_frame:
                        raise
                    tolerated_bad_frame = True
                    log.warning("tolerated one bad frame from %s: %s", client.label, exc)
                    await client.send_error(exc.code, exc.detail or exc.reason)
                    continue
                client.touch()             # A17：任何帧都刷新 last_active
                log.debug("recv type=0x%02X body=%s", msg_type, body)
                await self.router.dispatch(client, msg_type, body)

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
            self._live -= 1                    # 这条连接真的走完了，空房计时可以开始
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
            note = client.leave_note or LEAVE_TEXT.get(client.leave_reason, "离线了")
            await self._system(f"{nickname} {note}",
                               level="info" if client.leave_reason == "logout" else "warn")
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
