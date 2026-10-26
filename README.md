# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 模块 | 状态 |
| --- | --- |
| 应用层协议 v2（15 种类型、10 个错误码） | ✅ 已冻结（tag `proto-v2`） |
| 帧编解码 + 93 条单元/联调测试 | ✅ |
| 服务端：昵称登录、群聊、一对一私聊、颜色广播、上下线通知 | ✅ |
| 服务端：登录时限 10 s、未登录 1006、滑动窗口限流 2002、系统消息 `CHAT_SYSTEM` | ✅ |
| 心跳与超时踢除（15 s / 45 s / 5 s 扫描） | ✅ |
| 断线与写失败处理（写超时 5 s、立即断开、不留幽灵用户） | ✅ |
| 客户端网络层（独立线程 + 事件队列，CLI 与 GUI 共用） | ✅ |
| GUI：登录窗 + 四区主窗 + 私聊小窗 + 设置面板 + 断线横幅 | ✅ |
| 命令行客户端（`/w` 私聊、`/color` 改色、`/who`、心跳保活） | ✅ |
| 验收用例 T01—T13（《结果示例》第 5 章） | T01—T12 已在协议／CLI／GUI 层验过（见 `docs/test/D3.md`）；T13「换网段」的**换 IP 部分**已在手机热点（172.20.10.5）上验完，只剩「第二台机器」现场走一遍 |

## 环境

本机的 `python` 是 Windows 商店占位符（`python --version` 返回 9009），**请一律用 `py`**：

```powershell
py -3.13 -V            # Python 3.13.7
.venv\Scripts\Activate.ps1
```

控制台默认 GBK，打印 emoji 会炸。所有入口都调 `shared.console.setup_console()`
把三个标准流切到 UTF-8。

## 目录

```
Bluebird/
├─ 启动聊天室.bat    双击即用：起服务端 + 打开登录窗（不需要快捷方式、不需要装依赖）
├─ 创建桌面快捷方式.bat  双击在仓库根与桌面各生成一个 .lnk（.lnk 不进仓库，见下）
├─ start_chat.py    一键启动：没有服务端就先起一个，再打开登录窗（快捷方式指向它）
├─ assets/
│   └─ bluebird.ico     快捷方式图标（tools/make_icon.py 生成）
├─ shared/        协议、配置、错误码（前后端共用）
│   ├─ config.py        命令行参数：--host / --port / --max-frame / --log-level
│   ├─ protocol.py      帧编解码、MsgType、颜色常量、ProtocolError
│   ├─ errors.py        10 个错误码 + 中文文案 + FATAL_CODES
│   └─ console.py       Windows 控制台 UTF-8 适配
├─ server/
│   ├─ server.py        登录、群聊、私聊、颜色、上下线
│   ├─ router.py        type → 处理函数（装饰器注册；未知类型回 2004）
│   ├─ heartbeat.py     心跳判死的纯函数（45 s = 3 × 15 s）
│   ├─ ratelimit.py     单用户滑动窗口（10 条/秒）
│   └─ connection.py    单连接封装：nickname / color / last_active / send / abort
├─ client/
│   ├─ net.py           网络层：一条连接 + 心跳 + 事件队列（CLI 与 GUI 共用）
│   ├─ cli.py           命令行客户端（只做渲染）
│   ├─ gui.py           图形界面入口：登录窗、DPI、事件泵、断线回登录
│   ├─ chatui.py        四区主窗：顶栏 / 消息区 / 在线列表 / 输入区
│   ├─ dialogs.py       私聊窗（Toplevel）与设置面板（四色）
│   └─ ui.py            界面公共零件：字体、四色、只读消息框、贴底规则
├─ tools/
│   ├─ procutil.py      起停子进程（Windows 上要杀整棵树，否则启动器留下真进程）
│   ├─ tap.py           应用层抓包代理（按协议拆帧，逐帧记录十六进制）
│   ├─ capture_d1.py    D1 取证：一条命令起服务端 + tap + 两个客户端
│   ├─ capture_d2.py    D2 取证：三条连接各一个 tap，证明私聊不外泄（TC-05）
│   ├─ tap_to_pcap.py   tap 日志 → 可在 Wireshark 里看的 pcap（载荷原样，头部重建）
│   ├─ verify_d1.py     D1 验收：13 项协议层判据
│   ├─ verify_d1_cli.py D1 验收：两个真实 CLI 客户端互发
│   ├─ verify_d2.py     D2 验收：20 项协议层判据（登录／名单／群聊／私聊／限流）
│   ├─ verify_d2_cli.py D2 验收：三端真实 CLI
│   ├─ verify_robust.py 健壮性验收：心跳判死、慢客户端、坏帧容错、断线退出
│   ├─ verify_d3_gui.py D3 验收：开真窗口走查 34 项，可 --shots 出截图、--host 换地址
│   ├─ verify_d3_net.py D3 验收：T13 换网复验（走真实网卡，不经过 127.0.0.1）
│   ├─ make_shortcut.py 生成「Bluebird 聊天室」快捷方式（仓库根 + 桌面）
│   ├─ make_icon.py     生成快捷方式图标 assets/bluebird.ico
│   └─ build_d3_doc.py  生成桌面工作汇总 docx
├─ tests/               协议 23 + 限流 4 + 路由 6 + 心跳 6 + 服务端 32 + 客户端 16 + 界面 6 = 93 条
└─ docs/
    ├─ protocol.md      应用层协议 v2（唯一事实源）
    ├─ 三阶段实施路线.md
    ├─ pcap/            抓包日志（v1/v2）与帧结构配图
    ├─ gui/             D3 界面截图（登录 / 主窗 / 私聊 / 设置 / 断线）
    └─ test/            D1/D2/D3 验收记录与证据
```

## 快速开始

### 从 GitHub 下载之后怎么用

**仓库里没有 `.lnk` 快捷方式，这是故意的**：`.lnk` 存的是绝对路径
（`D:\Bluebird\.venv\Scripts\pythonw.exe` 之类），传到别人机器上必然指向不存在的位置，
所以它被 `.gitignore` 排除，只能在本机生成。下载后按需要双击下面两个之一：

| 想做的事 | 双击这个 | 会发生什么 |
| --- | --- | --- |
| 直接开聊 | `启动聊天室.bat` | 起服务端（若没有）+ 弹出登录窗，不用先做快捷方式 |
| 弄一个桌面图标 | `创建桌面快捷方式.bat` | 在仓库根目录和桌面各生成一个「Bluebird 聊天室」 |

两个脚本都只用标准库，**不需要 pip install**；有 `.venv` 就用它，没有就用系统里的
Python（要求 3.10+，安装时记得勾选 “Add python.exe to PATH”）。

### 有了快捷方式之后

双击「Bluebird 聊天室」：

- 8888 上没有服务端 → 后台起一个（日志 `logs/server.log`），随后弹出登录窗；
- 已经有服务端 → 只多开一个客户端窗口（演示三端就双击三次）；
- 关掉所有窗口后，服务端**最后一个客户端离开 2 分钟自动退出**（`--idle-exit 120`），
  不会留下看不见的后台进程。

要连别人机器上的服务端：双击后把登录窗里的地址改成对方 IP 即可；
也可以直接开一个只连服务端的客户端：

```powershell
py -3.13 tools\make_shortcut.py          # 重建快捷方式（仓库根 + 桌面）
py -3.13 start_chat.py --no-server       # 只开客户端（连别人机器）
py -3.13 start_chat.py --host 192.168.1.10
```

> 快捷方式里存的是绝对路径：**把仓库挪了位置就要重新跑一次 `创建桌面快捷方式.bat`**。

### 图形界面（手动起）

先起服务端，再开三个客户端窗口。

```powershell
py -3.13 -m server.server                          # 终端 1：监听 0.0.0.0:8888
py -3.13 -m client.gui --name alice                # 终端 2：弹出登录窗
py -3.13 -m client.gui --name bob                  # 终端 3（换机器时 --host 填服务端 IP）
```

登录窗里填昵称与服务器地址 → 进入 → 双击右侧昵称开私聊 → ⚙ 设置里换颜色。

**命令行界面**（脚本 / 抓包 / 三端快速验证用）：

```powershell
py -3.13 -m server.server                          # 终端 1：监听 0.0.0.0:8888
py -3.13 -m client.cli --name alice                # 终端 2
py -3.13 -m client.cli --name bob --color blue     # 终端 3（可换机器，--host 填服务端 IP）
```

进去先登录（昵称在线唯一），然后：

```
直接输入            群聊，格式 [昵称：内容]
/w bob 在吗         私聊 bob
/color green        改自己的颜色（black / green / blue / red，所有人可见）
/who                看在线名单
/quit               退出
```

服务端行为：

| 收到 | 动作 |
| --- | --- |
| `LOGIN_REQ` | 校验昵称格式与重名 → 回 `LOGIN_RESP` → 下发 `USER_LIST` → 广播 `USER_JOIN` 与一条 `CHAT_SYSTEM` |
| `LOGIN_REQ` 缺字段／类型不对 | 回 `LOGIN_RESP{2003}` 后断开（协议 §0 纪律 1） |
| 登录前发别的帧 / 10 s 不登录 | 分别回 `1006` 后断开、静默断开 |
| `CHAT_GROUP` | 限流通过 → 补 `seq/from/server_ts` → 广播给所有人（含发送者） |
| `CHAT_PRIVATE` | 按 `to` 单投；对象离线回 `3001`，不暂存 |
| `SET_COLOR` | 校验四色 → 广播 `USER_COLOR` |
| `PING` | 回 `PONG`；任何帧都刷新 `last_active` |
| 超过 10 条/秒 | 回 `2002`，这一条不扇出（滑动窗口，`server/ratelimit.py`） |
| 45 s 一个帧都没收到 | 判死：`USER_LEAVE{reason:"disconnect"}` + 系统提示「超时下线」 |
| 写不进去（对端不读／已断） | 5 s 写超时后立刻断开，绝不把整个房间拖住 |
| 未知类型 | 回 `ERROR(2004)`，连接保持 |
| 坏 JSON | 回 `2003` 容错一次，第二次才断 |
| 坏 magic / 版本不符 / 超长帧 | 前两者分别静默断开与回 `1003`，超长静默断开 |

## 测试

```powershell
.venv\Scripts\python.exe -m pytest                 # 93 passed
.venv\Scripts\python.exe tools\verify_d3_gui.py    # D3 界面走查 34 项（自己起服务端，约 20 秒）
.venv\Scripts\python.exe tools\verify_d3_gui.py --shots   # 顺便把截图写到 docs/gui/
.venv\Scripts\python.exe tools\verify_d3_gui.py --host 172.20.10.5   # 换网复验：走真实网卡
.venv\Scripts\python.exe tools\verify_d3_net.py    # T13 换网复验 11 项（自动取本机局域网 IP）
.venv\Scripts\python.exe tools\verify_robust.py    # 13 项：心跳判死、慢客户端、坏帧容错、断线退出
.venv\Scripts\python.exe tools\verify_robust.py --slow   # 再加 3 项：真实 15/45 s 参数下空闲 50 s（约 1 分钟）
.venv\Scripts\python.exe tools\verify_d2.py        # 三端协议层 20 项
.venv\Scripts\python.exe tools\verify_d2_cli.py    # 三端真实 CLI（需先起服务端）
.venv\Scripts\python.exe tools\capture_d2.py       # 私聊隔离抓包（TC-05，自起服务端与三个 tap）
```

## 协议速览

```
[ magic 2B ][ ver 1B ][ type 1B ][ length 4B 大端 ][ body: JSON, UTF-8 ]
  0x4348      0x02                                               ≤ 65528
```

15 种消息类型、10 个错误码、5 条关键时序，见 [docs/protocol.md](docs/protocol.md)。

## 文档

- [docs/protocol.md](docs/protocol.md) —— 应用层协议 v2（冻结版）
- [docs/test/D1.md](docs/test/D1.md) —— D1 抓包记录（协议 v2）与 Wireshark 补图步骤
- [docs/test/D1-验收报告.md](docs/test/D1-验收报告.md) —— D1 验收结论与证据清单
- [docs/test/D2.md](docs/test/D2.md) —— D2 三端联调、TC-01—TC-08 与限流/登录硬化记录
- [docs/test/D2-审查.md](docs/test/D2-审查.md) —— D2 三轮审查记录（第三轮修掉 5 处会在 D3 引爆的隐患）
- [docs/test/D2-robust-evidence.txt](docs/test/D2-robust-evidence.txt) —— 健壮性判据的实际输出
- [docs/test/D2-capture.txt](docs/test/D2-capture.txt) —— 私聊隔离的字节级证据（carol 的流里 0 条 0x11）
- [docs/test/D3.md](docs/test/D3.md) —— D3 界面与健壮性：四区布局、三个决定、四个坑、换网复验
- [docs/test/D3-gui-evidence.txt](docs/test/D3-gui-evidence.txt) —— 界面走查 34 项的实际输出
- [docs/test/D3-net-evidence.txt](docs/test/D3-net-evidence.txt) —— T13 换网复验 11 项（真实网卡、非回环）
- [docs/gui/](docs/gui) —— 登录窗 / 主窗 / 私聊窗 / 设置 / 断线 五张截图
- [docs/三阶段实施路线.md](docs/三阶段实施路线.md) —— 三阶段施工顺序与每步判据

