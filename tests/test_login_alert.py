"""登录态缺失告警单测:profile 不存在时应抛 SystemExit 且落 ERROR 日志(alert 可扫到)。

背景:cron 下登录失败原本只进 stderr,notify alert 只认日志第 2 段字段,
微博/雪球整轮空跑用户毫无感知(2026-08-26 实际发生过)。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
import unittest
from pathlib import Path

from common import obs
from weibo import login as wb_login
from xueqiu import login as xq_login


class TestLoginErrorAlertable(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        old_cwd = os.getcwd()
        os.chdir(self._tmp.name)  # 日志写到临时目录,不污染仓库 logs/
        self.addCleanup(os.chdir, old_cwd)

    def tearDown(self) -> None:
        for name in ("weibo", "xueqiu"):
            logging.getLogger(name).handlers.clear()

    def _assert_alertable(self, module: str, fn, snippet: str) -> None:
        with self.assertRaises(SystemExit):
            fn(".no_such_profile")
        day = dt.date.today().isoformat()
        text = (Path("logs") / f"{module}-{day}.log").read_text(encoding="utf-8")
        self.assertIn("| ERROR | 登录 |", text)
        self.assertIn(snippet, text)
        # 闭环:notify alert 的扫描逻辑必须能把它当 issue 捞出来
        issues = obs.scan_issues([module], log_dir="logs", state_dir="logs", flag_dir="logs")
        self.assertTrue(any(i["kind"] == "error" and snippet in i["message"] for i in issues))

    def test_weibo_missing_profile_raises_and_logs_error(self) -> None:
        self._assert_alertable("weibo", wb_login.get_cookie_header, "还没登录")

    def test_xueqiu_missing_profile_raises_and_logs_error(self) -> None:
        self._assert_alertable("xueqiu", xq_login.get_cookie_header, "雪球还没登录")


if __name__ == "__main__":
    unittest.main()
