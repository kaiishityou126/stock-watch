"""obs 单测:日志按级别落盘、flag 读写、扫描按级别字段判定(不靠中文正则)、偏移量防重复。"""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from common import obs


class TestObs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        logger = logging.getLogger("mod")
        for h in logger.handlers:
            h.close()
        logger.handlers.clear()
        self.tmp.cleanup()

    def test_log_writes_level_and_step(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.ERROR, "抓取", "出错了")
        files = list(self.dir.glob("mod-*.log"))
        self.assertEqual(len(files), 1)
        line = files[0].read_text(encoding="utf-8").strip()
        self.assertIn("ERROR", line)
        self.assertIn("抓取", line)
        self.assertIn("出错了", line)

    def test_flag_roundtrip(self):
        self.assertFalse(obs.is_paused("mod", flag_dir=self.dir))
        obs.set_paused("mod", "连续登录失效", flag_dir=self.dir)
        self.assertTrue(obs.is_paused("mod", flag_dir=self.dir))
        obs.clear_paused("mod", flag_dir=self.dir)
        self.assertFalse(obs.is_paused("mod", flag_dir=self.dir))

    def test_scan_picks_error_by_level_field(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.INFO, "抓取", "正常")     # 含中文"错"字样也不该误报
        obs.log(logger, logging.WARNING, "抓取", "有点慢")
        obs.log(logger, logging.ERROR, "LLM", "调用失败")
        issues = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        kinds = [i["kind"] for i in issues]
        self.assertIn("error", kinds)
        errs = [i for i in issues if i["kind"] == "error"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["step"], "LLM")

    def test_scan_offset_no_duplicate(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.ERROR, "DB", "写失败")
        first = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        second = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        self.assertEqual(len([i for i in first if i["kind"] == "error"]), 1)
        self.assertEqual(len([i for i in second if i["kind"] == "error"]), 0)

    def test_scan_reports_paused(self):
        obs.set_paused("mod", "熔断了", flag_dir=self.dir)
        issues = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir, flag_dir=self.dir)
        self.assertTrue(any(i["kind"] == "paused" for i in issues))


if __name__ == "__main__":
    unittest.main()
