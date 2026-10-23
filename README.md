# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| A01 | 项目骨架与配置模块 `shared/config.py` | ✅ |
| A02 | 协议定稿 `docs/protocol.md`（tag `proto-v1`） | ✅ |
| A03 | 帧编解码 `shared/protocol.py` + 错误码 `shared/errors.py` | ✅ |
| A04 | 编解码单元测试（21 条，含变异检查） | ✅ |
| A05 | 转发服务端 `server/server.py` | ✅ |
| A06 | 命令行客户端 `client/cli.py` | ✅ |
| A07 | 双端联调 + 字节级抓包取证 `tools/tap.py` | ✅ |
| A08 | 消息路由分发 `server/router.py` + `server/connection.py` | ✅ |
| A09 | 连接表与在线状态 | 待做 |
| A10 | 登录与鉴权 | 待做 |

## 环境

本机的 `python` 是 Windows 商店占位符（`python --version` 返回 9009），**请一律用 `py`**：

```powershell
py -3.13 -V            # Python 3.13.7
py -3.13 -m venv .venv # 已建好，装过 pytest
.venv\Scripts\Activate.ps1
```

本机控制台编码是 GBK，打印 emoji 会炸。所有入口都调 `shared.console.setup_console()`
把三个标准流切到 UTF-8；用管道喂中文时，调用方也要把输出编码设成 UTF-8：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

## 目录

```
Bluebird/
├─ shared/        协议、配置、错误码（前后端共用）
│   ├─ config.py        命令行参数：--host / --port / --max-frame / --log-level
│   ├─ protocol.py      帧编解码：encode_frame / read_frame / ProtocolError / MsgType
│   ├─ errors.py        15 个错误码 + 中文文案 + FATAL_CODES
│   └─ console.py       Windows 控制台 UTF-8 适配（stdin/stdout/stderr）
├─ server/
│   ├─ server.py        监听、读帧、广播、生命周期
│   ├─ router.py        type → 处理函数（装饰器注册；未知类型回 2004）
│   └─ connection.py    单个连接的封装：send / send_error / close
├─ client/
│   └─ cli.py           命令行客户端：输入一行 → CHAT_GROUP，打印收到的帧
├─ tools/
│   └─ tap.py           应用层抓包代理（A07 取证用）
├─ tests/               单元测试与联调测试（34 条）
└─ docs/
    ├─ protocol.md      应用层协议 v1（已冻结，唯一事实源）
    ├─ 三阶段实施路线.md
    ├─ pcap/            抓包截图（D1-frame.png）
    └─ test/            测试记录（D1.md）
```

## 快速开始

开三个终端：

```powershell
py -3.13 -m server.server                          # 终端 1：监听 0.0.0.0:8888
py -3.13 -m client.cli --name alice                # 终端 2
py -3.13 -m client.cli --name bob                  # 终端 3（换机器时 --host 填服务端 IP）
```

alice 输入一行，两个终端都会出现同一帧：

```
14:58:38  ←  CHAT_GROUP   {"msg_id": "alice-000001", "text": "大家好，我是 alice"}
```

服务端行为：

| 收到 | 动作 |
| --- | --- |
| `CHAT_GROUP` | 广播给所有在线连接（含发送者，用于回显） |
| `PING` | 回 `PONG{server_time}` |
| 未知类型 | 回 `ERROR(2004)`，**连接保持** |
| 坏 magic / 超长帧 | 不回包，直接断开 |

测试与抓包：

```powershell
.venv\Scripts\python.exe -m pytest                 # 34 passed

# 抓包：客户端连 8889，tap 转发到 8888 并逐帧记录
py -3.13 -m tools.tap --log docs\pcap\D1-tap.log
py -3.13 -m client.cli --port 8889 --name alice
```

## 协议速览

```
[ magic 2B ][ ver 1B ][ type 1B ][ length 4B 大端 ][ body: JSON, UTF-8 ]
  0x4348      0x01                                               ≤ 65528
```

17 种消息类型、15 个错误码、5 条关键时序，全部见 [docs/protocol.md](docs/protocol.md)。

## 文档

- [docs/protocol.md](docs/protocol.md) —— 应用层协议 v1（冻结版）
- [docs/test/D1.md](docs/test/D1.md) —— D1 抓包记录与 Wireshark 复现步骤
- [docs/三阶段实施路线.md](docs/三阶段实施路线.md) —— 三阶段施工顺序与每步判据
