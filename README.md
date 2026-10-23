# Bluebird

同一局域网内的多用户聊天室 —— 计算机网络技术课程设计。

C/S 架构 · TCP 长连接 · 自定义应用层协议（8 字节帧头 + UTF-8 JSON）· asyncio · tkinter

## 进度

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| A01 | 项目骨架与配置模块 | ✅ |
| A02 | 协议定稿（`docs/protocol.md`，tag `proto-v1`） | ✅ |
| A03 | 帧编解码 `shared/protocol.py` | 待做 |
| A04 | 编解码单元测试 | 待做 |
| A05 | echo 服务端 | 待做 |
| A06 | CLI 客户端 | 待做 |

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
│   └─ config.py        命令行参数：--host / --port / --max-frame / --log-level
├─ server/      服务端：监听、路由、鉴权、在线状态、心跳、文件
├─ client/      客户端：CLI 与 tkinter GUI
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
