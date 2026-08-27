"""Hermes no-agent 定时脚本:盘后行情快照。

由 hermes cron --no-agent --script 调用;cwd 由 cron 的 --workdir 指定为
仓库根目录,故可直接 python -m market。只负责抓取,告警由 stockwatch_alert 统一处理。

解释器探测:本脚本可能跑在 Hermes venv(Python 3.11,常缺 akshare)下,
而行情抓取强依赖 akshare —— 先探测已装 akshare 的解释器(如系统 Python 3.12),
探测不到才退回 sys.executable,避免 "No module named 'akshare'" 空跑。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

PROBE_MODULE = "akshare"
FALLBACK_PYTHONS = [
    # 系统 Python(本项目依赖装在这里);可另加其他路径
    r"C:\Program Files\Python312\python.exe",
]


def _pick_python() -> str:
    """返回能 import probe_module 的解释器;找不到就退回 sys.executable。"""
    candidates = [sys.executable]
    for extra in FALLBACK_PYTHONS:
        if extra != sys.executable and os.path.exists(extra):
            candidates.append(extra)
    for exe in candidates:
        try:
            subprocess.run(
                [exe, "-c", f"import {PROBE_MODULE}"],
                check=True, capture_output=True, timeout=60,
            )
            return exe
        except Exception:  # noqa: BLE001
            continue
    return sys.executable


def main() -> int:
    py = _pick_python()
    return subprocess.run([py, "-m", "market", "snapshot"]).returncode


if __name__ == "__main__":
    sys.exit(main())
