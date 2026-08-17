"""Hermes no-agent 定时脚本:微博增量抓取。

由 hermes cron --no-agent --script 调用;cwd 由 cron 的 --workdir 指定为
仓库根目录,故可直接 python -m weibo。只负责抓取,告警由 stockwatch_alert 统一处理。
"""

from __future__ import annotations

import subprocess
import sys

CMD = [sys.executable, "-m", "weibo", "crawl", "--mode", "incremental"]


def main() -> int:
    return subprocess.run(CMD).returncode


if __name__ == "__main__":
    sys.exit(main())
