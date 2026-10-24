"""命令行客户端：登录、群聊、私聊、改色、心跳。

    py -3.13 -m client.cli --name alice                 # 连 127.0.0.1:8888
    py -3.13 -m client.cli --host 192.168.1.10 --name bob --color green

支持的命令：

    /w <昵称> <内容>    私聊，例如 /w bob 在吗
    /color <颜色>       改颜色：black / green / blue / red
    /who                看当前在线
    /quit               退出（等价于 Ctrl+C）

D3 起这一层只做**渲染**：连接、协议、心跳、断线判定都搬到了 ``client/net.py``，
GUI 用的是同一份网络层。两个必须记住的点：

1. 网络层在后台线程里跑，只往这里丢事件；本文件用 ``queue.Queue`` 收，
   绝不在网络线程里 print。
2. ``input()`` 是阻塞的，必须放守护线程——否则连接断了主线程退不出去
   （``asyncio.run`` 收尾会等线程池，这是 2026-10-24 复审抓到的 P0）。
"""

from __future__ import annotations

import queue
import threading
import time

from client.net import NetClient, reason_text
from shared.config import Config, build_parser
from shared.console import setup_console
from shared.errors import message_for
from shared.protocol import COLORS, DEFAULT_COLOR

QUIT_COMMANDS = {"/quit", "/exit", "/q"}

#: /quit 之后最多等这么久，让 LOGOUT 真的发出去、服务端把下线通知广播完。
#: 不等就会出现「carol 明明点了退出，别人却收到『离线了』而不是『退出了』」。
QUIT_GRACE = 2.0


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


class ChatCli:
    def __init__(self, config: Config, name: str, color: str = DEFAULT_COLOR) -> None:
        self.config = config
        self.name = name
        self.color = color
        self.online: dict[str, dict] = {}
        self.quitting = False
        self.events: queue.Queue = queue.Queue()
        self.net = NetClient(
            config.host, config.port, name, color,
            on_event=self.events.put, max_frame=config.max_frame,
        )

    # ---------------------------------------------------------------- 入口

    def run(self) -> int:
        self.net.start()
        code = self._await_login()
        if code != 0:
            self.net.stop()
            return 2

        target = f"{self.config.host}:{self.config.port}"
        print(f"已进入聊天室：{self.name}（颜色 {self.color}）　{target}")
        print("直接输入 = 群聊；/w 昵称 内容 = 私聊；/color 颜色；/who；/quit")
        self._start_stdin_pump()
        return self._loop()

    def _await_login(self) -> int:
        """等第一条 login_ok / login_fail。"""
        while True:
            try:
                kind, payload = self.events.get(timeout=30)
            except queue.Empty:
                print("登录超时：30 秒没有得到结果")
                return 2
            if kind == "login_ok":
                self.name = payload["nickname"]
                self.color = payload["color"]
                return 0
            if kind == "login_fail":
                print(f"登录失败（{payload['code']}）：{payload['message']}")
                return 2
            if kind == "disconnected":
                print(f"登录失败：{reason_text(payload.get('reason'))}")
                return 2

    def _start_stdin_pump(self) -> None:
        """阻塞的 input() 放进守护线程；退出时不会被它钉住。"""
        queue_ = self.events

        def pump() -> None:
            while True:
                try:
                    line = input()
                except (EOFError, KeyboardInterrupt):
                    queue_.put(("line", {"text": None}))
                    return
                queue_.put(("line", {"text": line}))

        threading.Thread(target=pump, name="stdin", daemon=True).start()

    # ---------------------------------------------------------------- 主循环

    def _loop(self) -> int:
        quit_deadline = None
        while True:
            try:
                kind, payload = self.events.get(timeout=0.2)
            except queue.Empty:
                if quit_deadline is not None and time.monotonic() > quit_deadline:
                    return 0
                continue
            if kind == "line":
                if payload["text"] is None:
                    print("\n[输入结束]")
                    return 0
                self._handle_line(payload["text"])
                if self.quitting:
                    # 别急着退出：LOGOUT 还在网络线程的命令队列里没发出去
                    quit_deadline = time.monotonic() + QUIT_GRACE
                continue
            self._render(kind, payload)
            if kind == "disconnected":
                print(f"[连接已断开] {reason_text(payload.get('reason'))}")
                return 0 if self.quitting else 1

    def _handle_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        if text in QUIT_COMMANDS:
            self.net.logout()
            self.quitting = True
            return
        if text.startswith("/w "):
            parts = text[3:].split(maxsplit=1)
            if len(parts) != 2:
                print("用法：/w 昵称 内容")
                return
            self.net.send_private(parts[0], parts[1])
            return
        if text.startswith("/color "):
            color = text[7:].strip()
            if color not in COLORS:
                print("颜色只能是：" + " / ".join(COLORS))
                return
            self.net.set_color(color)
            return
        if text == "/who":
            names = "、".join(f"{n}({u.get('color')})" for n, u in self.online.items())
            print(f"[在线 {len(self.online)} 人] {names or '（无）'}")
            return
        if text == "/help":
            print("直接输入=群聊；/w 昵称 内容=私聊；/color 颜色；/who；/quit")
            return
        self.net.send_group(text)

    # ---------------------------------------------------------------- 渲染

    def _render(self, kind: str, payload: dict) -> None:
        ts = _stamp()
        if kind == "message":
            if payload["kind"] == "group":
                print(f"{ts}  [{payload['from']}：{payload['text']}]")
            else:
                print(f"{ts}  [私聊 {payload['from']}→{payload['to']}：{payload['text']}]")
        elif kind == "system":
            print(f"{ts}  [system：{payload['text']}]")
        elif kind == "user_list":
            self.online = {u["nickname"]: u for u in payload["users"]}
            names = "、".join(f"{n}({u.get('color')})" for n, u in self.online.items())
            print(f"{ts}  [system：在线 {payload['count']} 人] {names}")
        elif kind == "user_join":
            self.online[payload["nickname"]] = payload
        elif kind == "user_leave":
            self.online.pop(payload.get("nickname", ""), None)
        elif kind == "user_color":
            nickname = payload.get("nickname")
            if nickname in self.online:
                self.online[nickname]["color"] = payload.get("color")
            print(f"{ts}  [system：{nickname} 的颜色改为 {payload.get('color')}]")
        elif kind == "error":
            code = payload.get("code")
            print(f"{ts}  [错误 {code}] {payload.get('message') or message_for(code)}")


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
    cli = ChatCli(config, name, ns.color)
    try:
        return cli.run()
    except KeyboardInterrupt:
        cli.net.stop()
        print("\n[已退出]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
