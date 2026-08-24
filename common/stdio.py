"""CLI 输出编码统一。

Windows 管道下 Python 默认用 GBK 写 stdout,下游(Hermes 终端工具/微信适配器)
按 UTF-8 读会拿到乱码——每个 CLI 的 main() 入口调一次 force_utf8_stdio()。
已关闭/分离的流会抛 ValueError,一并兜住。
"""

from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
