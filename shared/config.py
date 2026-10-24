"""全局配置：命令行参数 > 内置默认值。

只依赖标准库。服务端与客户端都从这里取配置，避免各写一套 argparse。

用法：
    py -3.13 -m shared.config                 # 打印生效配置
    py -3.13 -m shared.config --port 9000 --host 127.0.0.1
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

# ---- 内置默认值（协议文档 docs/protocol.md 第 8 章与此保持一致）----
DEFAULT_HOST = "0.0.0.0"          # 服务端监听所有网卡；客户端会被显式指定目标 IP
DEFAULT_PORT = 8888               # 聊天主通道
DEFAULT_MAX_FRAME = 65536         # 单帧上限（含 8 字节首部）
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# ---- 心跳与超时（协议第 4.6、8 章；服务端与客户端共用一份）----
HEARTBEAT_INTERVAL = 15.0         # 客户端多久发一次 PING
IDLE_TIMEOUT = 45.0               # 服务端多久收不到任何帧就判死（= 3 × 心跳）
SWEEP_INTERVAL = 5.0              # 服务端扫描周期（判死误差 ≤ 5 s）

# ---- 单次写超时（不是协议字段，是防止一个卡死的对端拖住整个房间）----
SEND_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class Config:
    """一次进程启动之后不再变化的配置。"""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_frame: int = DEFAULT_MAX_FRAME
    log_level: str = DEFAULT_LOG_LEVEL

    def describe(self) -> str:
        """启动时打印的一行生效配置（B9-04）。"""
        return (
            f"host={self.host} port={self.port} "
            f"max_frame={self.max_frame} log_level={self.log_level}"
        )


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"端口必须是整数：{value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"端口必须在 1—65535 之间：{port}")
    return port


def _log_level(value: str) -> str:
    level = value.upper()
    if level not in LOG_LEVELS:
        raise argparse.ArgumentTypeError(
            f"日志级别只能是 {'/'.join(LOG_LEVELS)}：{value!r}"
        )
    return level


def build_parser(prog: str = "bluebird", default_host: str = DEFAULT_HOST) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="局域网多用户聊天室（TCP 8888 + 8 字节帧头 + JSON）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host", default=default_host,
        help="监听/连接地址；服务端用 0.0.0.0 表示所有网卡，客户端填服务端 IP",
    )
    parser.add_argument(
        "--port", default=DEFAULT_PORT, type=_port,
        help="TCP 端口",
    )
    parser.add_argument(
        "--max-frame", default=DEFAULT_MAX_FRAME, type=int,
        help="单帧字节上限，超过即断开",
    )
    parser.add_argument(
        "--log-level", default=DEFAULT_LOG_LEVEL, type=_log_level,
        help="日志级别",
    )
    return parser


def parse_args(argv: list[str] | None = None, prog: str = "bluebird", default_host: str = DEFAULT_HOST) -> Config:
    """解析命令行参数；测试里传 argv 列表即可，不读真实 sys.argv。"""
    ns = build_parser(prog, default_host).parse_args(argv)
    return Config(
        host=ns.host,
        port=ns.port,
        max_frame=ns.max_frame,
        log_level=ns.log_level,
    )


if __name__ == "__main__":
    print(parse_args().describe())
