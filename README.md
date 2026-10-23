# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| A01 | 项目骨架与配置模块 | ✅ |
| A02 | 协议定稿（`docs/protocol.md`，tag `proto-v1`） | ✅ |
| A03 | 帧编解码 `shared/protocol.py` + 错误码 `shared/errors.py` | ✅ |
| A04 | 编解码单元测试（21 条，含变异检查） | ✅ |
| A05 | 转发服务端 `server/server.py`（含 6 条联调测试） | ✅ |
| A06 | 命令行客户端 `client/cli.py` | ✅ |
| A07 | 双机联调 + Wireshark 抓包取证 | 待做 |

## 环境

本机的 `python` 是 Windows 商店占位符（`python --version` 返回 9009），**请一律用 `py`**：

```powershell
py -3.13 -V            # Python 3.13.7
py -3.13 -m venv .venv # 建虚拟环境（可选）
.venv\Scripts\Activate.ps1
```

注意：本机控制台编码是 GBK，打印 emoji 或生僻字会炸。运行入口会显式把 stdout/stderr 切到 UTF-8，
需要保持终端一致时请先执行 `chcp 65001`。

## 目录

```
Bluebird/
├─ shared/      协议、配置、数据模型（前后端共用）
│   ├─ config.py        命令行参数：--host / --port / --max-frame / --log-level
│   ├─ protocol.py      帧编解码：encode_frame / read_frame / ProtocolError / MsgType
│   ├─ errors.py        错误码与中文文案（15 个码，前后端共用）
│   └─ console.py       Windows 控制台 UTF-8 适配（stdin/stdout/stderr）
├─ server/      服务端
│   └─ server.py        转发服务端：收一帧，转给所有在线客户端
├─ client/      客户端
│   └─ cli.py           命令行客户端：输入一行 → CHAT_GROUP，打印收到的帧
├─ tests/       单元测试与压测脚本
└─ docs/
    ├─ protocol.md      应用层协议 v1（已冻结，唯一事实源）
    ├─ 三阶段实施路线.md
    ├─ pcap/            抓包截图
    └─ test/            测试记录
```

## 现在能跑什么

```powershell
py -3.13 -m shared.config
# host=0.0.0.0 port=8888 max_frame=65536 log_level=INFO

py -3.13 -m shared.config --port 9000 --log-level debug
```

帧编解码（A03）已经有实现和测试：

```powershell
.venv\Scripts\python.exe -m pytest
# 21 passed
```

```python
from shared.protocol import MsgType, encode_frame, read_frame

frame = encode_frame(MsgType.PING)          # b'CH\x01\x30\x00\x00\x00\x02{}'
msg_type, body = await read_frame(reader)   # (0x30, {})
```

服务端与客户端（开三个终端）：

```powershell
py -3.13 -m server.server                          # 终端 1：监听 0.0.0.0:8888
py -3.13 -m client.cli --name alice                # 终端 2
py -3.13 -m client.cli --name bob                  # 终端 3（可换机器，--host 填服务端 IP）
```

alice 输入一行，alice 和 bob 的终端都会出现同一帧：

```
14:58:38  ←  CHAT_GROUP   {"msg_id": "alice-000001", "text": "大家好，我是 alice"}
```

> 用管道喂中文时（`echo 你好 | client`），先把 PowerShell 的输出编码设成 UTF-8：
> `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`，否则 GBK 字节会被按 UTF-8 解成乱码。
> 交互输入不受影响。

服务端与客户端在 A05 / A06 之后可用：

```powershell
py -3.13 -m server.server --host 0.0.0.0 --port 8888
py -3.13 -m client.cli --host 192.168.1.10 --port 8888
```

## 协议速览

```
[ magic 2B ][ ver 1B ][ type 1B ][ length 4B 大端 ][ body: JSON, UTF-8 ]
  0x4348      0x01                                               ≤ 65536 字节
```

17 种消息类型、15 个错误码、5 条关键时序，全部见 [docs/protocol.md](docs/protocol.md)。

## 文档

- [docs/protocol.md](docs/protocol.md) —— 应用层协议 v1（冻结版）
- [docs/三阶段实施路线.md](docs/三阶段实施路线.md) —— 三阶段施工顺序与每步判据
