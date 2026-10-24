"""客户端网络层：一条连接、一个 asyncio 循环、一个事件出口。

为什么单独一层：D3 起有两个界面（命令行和 tkinter），它们不该各写一套重连、
心跳、拆帧、错误映射。这一层只做「协议与连接」，**不打印、不碰控件**，
结果统一从 ``on_event`` 回调出去。

线程模型（GUI 必须照这个用，否则随机崩）::

    GUI 线程 ──调用 send_group()/set_color()──► [命令队列]
                                                    │ call_soon_threadsafe
    网络线程：asyncio 循环 ──读帧/心跳/写帧─────────┘
                    │ on_event(kind, payload)
                    ▼
    GUI 线程 ◄── queue.Queue ◄── (适配器) ◄──────────┘
                    │
                    └─ root.after(50) 轮询 → 只在主线程碰控件

事件种类（``kind`` → ``payload``）：

    login_ok      {nickname, color}
    login_fail    {code, message}
    user_list     {users: [{nickname, color, since}], count}
    user_join     {nickname, color, since}
    user_leave    {nickname, reason}
    user_color    {nickname, color}
    message       {kind: "group"|"private", from, to?, text, seq, server_ts}
    system        {text, level, ts}
    error         {code, message}
    disconnected  {reason: server_closed|network|timeout|protocol|send_failed}

``disconnected`` 之后这一层就收工了，要再来一次就新建一个实例——v2 没有会话续期，
断线就是回登录窗重新走一遍（《结果示例》决定⑥）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

from shared.config import (DEFAULT_MAX_FRAME, HEARTBEAT_INTERVAL, IDLE_TIMEOUT, SEND_TIMEOUT)
from shared.errors import ErrorCode
from shared.protocol import (COLORS, DEFAULT_COLOR, HEADER_SIZE, MsgType, ProtocolError,
                             encode_frame, read_frame)

Event = tuple[str, dict[str, Any]]
Listener = Callable[[Event], None]

CONNECT_TIMEOUT = 5.0
LOGIN_TIMEOUT = 5.0

#: 断线原因 → 中文说法（A24：别把 Python 异常名甩到用户脸上）。
#: 命令行与 GUI 共用这一份，免得同一个原因在两个界面上是两种说法。
REASON_TEXT: dict[str, str] = {
    "server_closed": "服务端关闭了连接",
    "network": "网络中断",
    "timeout": "长时间收不到服务端数据，判定断线",
    "protocol": "协议错误",
    "send_failed": "发送失败，连接已不可用",
    "crash": "客户端内部错误",
}


def reason_text(reason: str | None) -> str:
    return REASON_TEXT.get(reason or "", "连接中断")


class NetClient:
    """一条聊天连接。``start()`` 之后在后台线程里跑，直到断开或 ``stop()``。"""

    def __init__(
        self,
        host: str,
        port: int,
        name: str,
        color: str = DEFAULT_COLOR,
        on_event: Listener | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        login_timeout: float = LOGIN_TIMEOUT,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        idle_timeout: float = IDLE_TIMEOUT,
        max_frame: int = DEFAULT_MAX_FRAME,
    ) -> None:
        self.host = host
        self.port = port
        self.name = name
        self.color = color if color in COLORS else DEFAULT_COLOR
        self.on_event = on_event
        self.connect_timeout = connect_timeout
        self.login_timeout = login_timeout
        self.heartbeat_interval = heartbeat_interval
        self.idle_timeout = idle_timeout
        self.max_body = max(1, max_frame - HEADER_SIZE)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._reported_disconnect = False
        self._last_rx = time.monotonic()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._sending_lock = threading.Lock()

    # ---------------------------------------------------------------- 对外

    @property
    def running(self) -> bool:
        return self._ready.is_set() and not self._closed.is_set()

    def start(self) -> None:
        """起后台线程；登录结果和后续消息都从 ``on_event`` 出来。"""
        if self._thread is not None:
            raise RuntimeError("同一个 NetClient 只能 start 一次")
        self._thread = threading.Thread(target=self._thread_main, name="net", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """让网络线程收工。可以在任意线程调，重复调安全。"""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._request_stop)
            except RuntimeError:
                pass

    def send_group(self, text: str) -> None:
        self._submit(("group", {"text": text}))

    def send_private(self, to: str, text: str) -> None:
        self._submit(("private", {"to": to, "text": text}))

    def set_color(self, color: str) -> None:
        self._submit(("color", {"color": color}))

    def logout(self) -> None:
        self._submit(("logout", {}))

    # ---------------------------------------------------------------- 线程

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except asyncio.CancelledError:
            pass                                   # stop() 主动取消，正常收工
        except Exception as exc:                   # 兜底：线程不能悄悄死
            self._emit("disconnected", {"reason": "crash", "detail": repr(exc)})
        finally:
            self._closed.set()
            self._ready.set()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def _request_stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    # ---------------------------------------------------------------- 主流程

    async def _run(self) -> None:
        self._commands = asyncio.Queue()
        self._task = asyncio.current_task()
        writer = None
        try:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.connect_timeout,
                )
            except asyncio.TimeoutError:
                self._emit("login_fail", {"code": -1, "message": "连接超时，服务端没有响应"})
                return
            except ConnectionRefusedError:
                self._emit("login_fail", {"code": -1,
                                          "message": "连接被拒绝：该地址没有程序在监听"})
                return
            except OSError as exc:
                self._emit("login_fail", {"code": -1, "message": f"连接失败：{exc}"})
                return

            self.reader = reader
            self.writer = writer
            self._ready.set()

            if not await self._login():
                return

            tasks = [
                asyncio.create_task(self._recv_loop(), name="recv"),
                asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
                asyncio.create_task(self._command_loop(), name="commands"),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    self._emit("disconnected", {"reason": "crash", "detail": repr(exc)})
        finally:
            self._closed.set()
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), SEND_TIMEOUT)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass

    async def _login(self) -> bool:
        self.writer.write(encode_frame(MsgType.LOGIN_REQ, {
            "username": self.name,
            "client_ver": "2.0",
            "color": self.color,
        }))
        await self.writer.drain()
        try:
            msg_type, body = await asyncio.wait_for(
                read_frame(self.reader, self.max_body), self.login_timeout
            )
        except asyncio.TimeoutError:
            self._emit("login_fail", {"code": -1, "message": "登录超时：服务端没有回应"})
            return False
        except asyncio.IncompleteReadError:
            self._emit("login_fail", {"code": -1, "message": "登录失败：服务端关闭了连接"})
            return False
        except (ConnectionError, OSError) as exc:
            self._emit("login_fail", {"code": -1,
                                      "message": f"登录失败：网络中断（{exc.__class__.__name__}）"})
            return False
        except ProtocolError as exc:
            self._emit("login_fail", {"code": -1, "message": f"登录失败：{exc.detail or exc.reason}"})
            return False

        if msg_type != MsgType.LOGIN_RESP:
            self._emit("login_fail", {"code": -1, "message": "登录失败：服务端回了意料之外的帧"})
            return False

        code = int(body.get("code", -1))
        if code != 0:
            self._emit("login_fail", {"code": code, "message": body.get("message", "未知原因")})
            return False

        self.name = body.get("nickname", self.name)
        self.color = body.get("color", self.color)
        self._last_rx = time.monotonic()
        self._emit("login_ok", {"nickname": self.name, "color": self.color})
        return True

    # ---------------------------------------------------------------- 收发

    async def _recv_loop(self) -> None:
        while True:
            try:
                msg_type, body = await read_frame(self.reader, self.max_body)
            except asyncio.IncompleteReadError:
                self._emit("disconnected", {"reason": "server_closed"})
                return
            except (ConnectionError, OSError):
                self._emit("disconnected", {"reason": "network"})
                return
            except ProtocolError as exc:
                self._emit("disconnected", {"reason": "protocol",
                                            "detail": exc.detail or exc.reason})
                return
            self._last_rx = time.monotonic()
            self._dispatch(msg_type, body)

    async def _heartbeat_loop(self) -> None:
        """每 15 s 一个 PING；45 s 收不到任何帧判死（= 连丢 3 个 PONG）。"""
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            if time.monotonic() - self._last_rx > self.idle_timeout:
                self._emit("disconnected", {"reason": "timeout"})
                return
            if not await self._write(MsgType.PING):
                return

    async def _command_loop(self) -> None:
        while True:
            kind, payload = await self._commands.get()
            if kind == "group":
                if not await self._write(MsgType.CHAT_GROUP,
                                         {"msg_id": self._next_msg_id(), "text": payload["text"]}):
                    return
            elif kind == "private":
                if not await self._write(MsgType.CHAT_PRIVATE, {
                    "msg_id": self._next_msg_id(), "to": payload["to"], "text": payload["text"],
                }):
                    return
            elif kind == "color":
                if not await self._write(MsgType.SET_COLOR, {"color": payload["color"]}):
                    return
                self.color = payload["color"]
            elif kind == "logout":
                await self._write(MsgType.LOGOUT)
                return

    async def _write(self, msg_type: int | MsgType, body: dict | None = None) -> bool:
        """写一帧；失败就上报断线并返回 False（调用方据此收工）。"""
        try:
            self.writer.write(encode_frame(msg_type, body if body is not None else {}))
            await asyncio.wait_for(self.writer.drain(), SEND_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            self._emit("disconnected", {"reason": "send_failed",
                                        "detail": f"对端 {SEND_TIMEOUT:.0f} 秒不收数据"})
            return False
        except (ValueError, ConnectionError, OSError) as exc:
            self._emit("disconnected", {"reason": "send_failed", "detail": str(exc)})
            return False

    def _dispatch(self, msg_type: int, body: dict) -> None:
        if msg_type == MsgType.CHAT_GROUP:
            self._emit("message", {"kind": "group", "from": body.get("from"),
                                   "text": body.get("text", ""), "seq": body.get("seq"),
                                   "server_ts": body.get("server_ts")})
        elif msg_type == MsgType.CHAT_PRIVATE:
            self._emit("message", {"kind": "private", "from": body.get("from"),
                                   "to": body.get("to"), "text": body.get("text", ""),
                                   "seq": body.get("seq"), "server_ts": body.get("server_ts")})
        elif msg_type == MsgType.CHAT_SYSTEM:
            self._emit("system", {"text": body.get("text", ""), "level": body.get("level", "info"),
                                  "ts": body.get("ts")})
        elif msg_type == MsgType.USER_LIST:
            self._emit("user_list", {"users": body.get("users", []),
                                     "count": body.get("count", len(body.get("users", [])))})
        elif msg_type == MsgType.USER_JOIN:
            self._emit("user_join", {"nickname": body.get("nickname"), "color": body.get("color"),
                                     "since": body.get("since")})
        elif msg_type == MsgType.USER_LEAVE:
            self._emit("user_leave", {"nickname": body.get("nickname"),
                                      "reason": body.get("reason")})
        elif msg_type == MsgType.USER_COLOR:
            self._emit("user_color", {"nickname": body.get("nickname"),
                                      "color": body.get("color")})
        elif msg_type == MsgType.ERROR:
            self._emit("error", {"code": body.get("code"), "message": body.get("message")})
        elif msg_type == MsgType.PONG:
            pass                                  # 刷新 last_active 就够了
        # 其余类型（ACK 等）本期不用，安静忽略

    # ---------------------------------------------------------------- 工具

    def _next_msg_id(self) -> str:
        with self._sending_lock:
            self._seq += 1
            return f"{self.name}-{self._seq:06d}"

    def _submit(self, command: tuple[str, dict]) -> None:
        if self._closed.is_set():
            return
        loop = self._loop
        commands = self._commands
        if loop is None or commands is None:
            return
        try:
            loop.call_soon_threadsafe(commands.put_nowait, command)
        except RuntimeError:                      # 循环已经关了
            pass

    def _emit(self, kind: str, payload: dict) -> None:
        if kind == "disconnected":
            if self._reported_disconnect:          # 收尾阶段可能被多处触发，只报一次
                return
            self._reported_disconnect = True
        if self.on_event is not None:
            self.on_event((kind, payload))


__all__ = ["NetClient", "Event", "Listener", "REASON_TEXT", "reason_text"]
