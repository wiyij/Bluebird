"""控制台编码适配。

Windows 控制台默认是 GBK（本机 ``sys.stdout.encoding == 'gbk'``），
打印 emoji 或生僻字会直接抛 ``UnicodeEncodeError``，把好好的服务端炸掉。

入口处调用一次 ``setup_console()``，把三个标准流都切到 UTF-8：
- stdout / stderr：宁可显示一个 ``�``，也不要因为一条日志崩掉进程；
- stdin：交互输入走 Windows 控制台 API 不受影响，但**管道输入**会按这个编码解码，
  不统一的话 `echo 你好 | client` 会解出孤立代理字符，后面一序列化就炸。
"""

from __future__ import annotations

import sys


def setup_console(encoding: str = "utf-8") -> None:
    """把标准输入/输出/错误统一到 UTF-8；不支持的流跳过。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=encoding, errors="replace")
        except (ValueError, OSError):  # 被重定向到不支持重配置的流
            pass


__all__ = ["setup_console"]
