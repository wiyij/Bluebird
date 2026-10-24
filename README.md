# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 模块 | 状态 |
| --- | --- |
| 应用层协议 v2（15 种类型、10 个错误码） | ✅ 已冻结（tag `proto-v2`） |
| 帧编解码 + 67 条单元/联调测试 | ✅ |
| 服务端：昵称登录、群聊、一对一私聊、颜色广播、上下线通知 | ✅ |
| 服务端：登录时限 10 s、未登录 1006、滑动窗口限流 2002、系统消息 `CHAT_SYSTEM` | ✅ |
| 心跳与超时踢除（15 s / 45 s / 5 s 扫描） | ✅ |
| 断线与写失败处理（写超时 5 s、立即断开、不留幽灵用户） | ✅ |
| 命令行客户端（`/w` 私聊、`/color` 改色、`/who`、心跳保活） | ✅ |
| GUI：登录窗 + 四区主窗 + 私聊小窗 | 待做（D3） |
| 验收用例 T01—T13（《结果示例》第 5 章） | 协议／CLI 层已过 T01—T05、T08、T10—T12；T06、T07（贴底滚动）、T09（设置面板）要等 GUI，T13（换网段）要真机 —— 都归 D3／D4 |

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
│   └─ cli.py           命令行客户端（GUI 未开始）
├─ tools/
│   ├─ tap.py           应用层抓包代理（按协议拆帧，逐帧记录十六进制）
│   ├─ capture_d1.py    D1 取证：一条命令起服务端 + tap + 两个客户端
│   ├─ capture_d2.py    D2 取证：三条连接各一个 tap，证明私聊不外泄（TC-05）
│   ├─ tap_to_pcap.py   tap 日志 → 可在 Wireshark 里看的 pcap（载荷原样，头部重建）
│   ├─ verify_d1.py     D1 验收：13 项协议层判据
│   ├─ verify_d1_cli.py D1 验收：两个真实 CLI 客户端互发
│   ├─ verify_d2.py     D2 验收：20 项协议层判据（登录／名单／群聊／私聊／限流）
│   ├─ verify_d2_cli.py D2 验收：三端真实 CLI
│   └─ verify_robust.py 健壮性验收：心跳判死、慢客户端、坏帧容错、断线退出
├─ tests/               协议 23 + 限流 4 + 路由 6 + 心跳 6 + 服务端 28 = 67 条
└─ docs/
    ├─ protocol.md      应用层协议 v2（唯一事实源）
    ├─ 三阶段实施路线.md
    ├─ pcap/            抓包日志（v1/v2）与帧结构配图
    └─ test/            D1 抓包记录、验收报告与证据
```

## 快速开始

开三个终端：

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
.venv\Scripts\python.exe -m pytest                 # 67 passed
.venv\Scripts\python.exe tools\verify_robust.py    # 13 项：心跳判死、慢客户端、坏帧容错、断线退出
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
- [docs/三阶段实施路线.md](docs/三阶段实施路线.md) —— 三阶段施工顺序与每步判据

