# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 模块 | 状态 |
| --- | --- |
| 应用层协议 v2（15 种类型、10 个错误码） | ✅ 已冻结（tag `proto-v2`） |
| 帧编解码 + 43 条单元/联调测试 | ✅ |
| 服务端：昵称登录、群聊、一对一私聊、颜色广播、上下线通知 | ✅ |
| 命令行客户端（`/w` 私聊、`/color` 改色、`/who`） | ✅ |
| 心跳与超时踢除 | 待做 |
| GUI：登录窗 + 四区主窗 + 私聊小窗 | 待做 |
| 验收用例 T01—T13（见规格文档） | 待做 |

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
│   └─ connection.py    单连接封装：nickname / color / send / close
├─ client/
│   └─ cli.py           命令行客户端（GUI 未开始）
├─ tools/
│   ├─ tap.py           应用层抓包代理（按协议拆帧，逐帧记录十六进制）
│   ├─ capture_d1.py    D1 取证：一条命令起服务端 + tap + 两个客户端
│   ├─ tap_to_pcap.py   tap 日志 → 可在 Wireshark 里看的 pcap（载荷原样，头部重建）
│   ├─ verify_d1.py     D1 验收：13 项协议层判据
│   └─ verify_d1_cli.py D1 验收：两个真实 CLI 客户端互发
├─ tests/               协议 23 条 + 路由 6 条 + 服务端 14 条
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
| `LOGIN_REQ` | 校验昵称格式与重名 → 回 `LOGIN_RESP` → 下发 `USER_LIST` → 广播 `USER_JOIN` |
| `CHAT_GROUP` | 补 `seq/from/server_ts` → 广播给所有人（含发送者） |
| `CHAT_PRIVATE` | 按 `to` 单投；对象离线回 `3001`，不暂存 |
| `SET_COLOR` | 校验四色 → 广播 `USER_COLOR` |
| 未知类型 | 回 `ERROR(2004)`，连接保持 |
| 坏 magic / 版本不符 / 超长帧 | 前两者分别静默断开与回 `1003`，超长静默断开 |

## 测试

```powershell
.venv\Scripts\python.exe -m pytest        # 43 passed
.venv\Scripts\python.exe -m pytest tests/test_server.py -v
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
- [docs/三阶段实施路线.md](docs/三阶段实施路线.md) —— 三阶段施工顺序与每步判据

