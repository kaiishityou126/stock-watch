"""告警决策单测:根据 scan_issues 结果决定是否推 + 组装告警文案(不真推)。"""
from __future__ import annotations

import unittest

from notify.cli import format_alert


class TestNotifyCli(unittest.TestCase):
    def test_no_issue_returns_none(self):
        self.assertIsNone(format_alert([]))

    def test_issues_grouped_text(self):
        issues = [
            {"module": "weibo", "kind": "paused", "step": "熔断", "message": "连续 3 次 401/403"},
            {"module": "report", "kind": "error", "step": "LLM", "message": "调用失败"},
        ]
        text = format_alert(issues)
        self.assertIsNotNone(text)
        self.assertIn("weibo", text)
        self.assertIn("熔断", text)
        self.assertIn("LLM", text)


if __name__ == "__main__":
    unittest.main()
