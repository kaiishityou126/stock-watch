"""Hermes no-agent 定时脚本:日志监控告警。

调用 python -m notify alert 扫描各模块 ERROR 日志 + 熔断 flag,
stdout 交给 Hermes 负责投递(空输出 = 静默,非空 = 推飞书)。

cwd 由 cron 的 --workdir 指定为仓库根目录。
"""

from __future__ import annotations

import subprocess
import sys

CMD = [sys.executable, "-m", "notify", "alert"]


def main() -> int:
    return subprocess.run(CMD).returncode


if __name__ == "__main__":
    sys.exit(main())
