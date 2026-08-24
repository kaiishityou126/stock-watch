"""market CLI 纯函数单测。"""
from __future__ import annotations

import unittest

from market.cli import _total_turnover


class TestTotalTurnover(unittest.TestCase):
    def test_requires_both_legs(self):
        # 两腿齐全才出数;缺一腿的"半边和"会被 upsert 的 COALESCE 固化成常驻错值
        total = _total_turnover([
            {"index_code": "sh000001", "turnover": 6.0e11},
            {"index_code": "sz399001", "turnover": 4.0e11},
            {"index_code": "sz399006", "turnover": 1.0e11},  # 深子集,不重复计
        ])
        self.assertAlmostEqual(total, 1.0e12)
        self.assertIsNone(_total_turnover([{"index_code": "sh000001", "turnover": 6.0e11}]))
        self.assertIsNone(_total_turnover([]))


if __name__ == "__main__":
    unittest.main()
