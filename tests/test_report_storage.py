"""简报存储层单测:按日期唯一、可标记已推送、可读回。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from report.storage import ReportStorage


class TestReportStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReportStorage(str(Path(self.tmp.name) / "app.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_save_and_get(self):
        self.store.save("2026-07-16", "今日观察……", "test-model", '{"weibo":3}', "now")
        r = self.store.get("2026-07-16")
        self.assertEqual(r["content"], "今日观察……")
        self.assertEqual(r["pushed"], 0)

    def test_save_overwrites_same_date(self):
        self.store.save("2026-07-16", "v1", "m", "{}", "now")
        self.store.save("2026-07-16", "v2", "m", "{}", "now")
        self.assertEqual(self.store.get("2026-07-16")["content"], "v2")

    def test_mark_pushed(self):
        self.store.save("2026-07-16", "x", "m", "{}", "now")
        self.store.mark_pushed("2026-07-16")
        self.assertEqual(self.store.get("2026-07-16")["pushed"], 1)


if __name__ == "__main__":
    unittest.main()
