"""行情存储层单测:快照按 (日期,板块) 去重;龙虎榜按 (日期,代码,原因) 去重;近似字段名带 _est。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.storage import MarketStorage


class TestMarketStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketStorage(str(Path(self.tmp.name) / "app.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _snap_row(self, **over):
        row = {"trade_date": "20260716", "sector": "半导体", "sector_type": "行业",
               "pct_change": 1.2, "turnover": None, "volume": None,
               "super_large_net": 6e7, "large_net": 4e7, "medium_net": -1e7, "small_net": -1e7,
               "main_net_inflow_est": 1e8, "retail_net_inflow_est": -2e7,
               "source": "akshare", "fetched_at": "now"}
        row.update(over)
        return row

    def test_snapshot_dedup(self):
        row = self._snap_row()
        self.store.upsert_snapshot([row, self._snap_row(pct_change=1.5)])
        rows = self.store.snapshot_for("20260716")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pct_change"], 1.5)  # 覆盖为最新
        self.assertIn("main_net_inflow_est", rows[0].keys())
        self.assertAlmostEqual(rows[0]["super_large_net"], 6e7)

    def test_snapshot_sector_type_in_pk(self):
        # 同板块名不同 sector_type(行业/概念)应共存,不互相覆盖
        self.store.upsert_snapshot([
            self._snap_row(sector="半导体", sector_type="行业", pct_change=1.0),
            self._snap_row(sector="半导体", sector_type="概念", pct_change=2.0),
        ])
        rows = self.store.snapshot_for("20260716")
        self.assertEqual(len(rows), 2)

    def test_index_dedup(self):
        row = {"trade_date": "20260716", "index_code": "sh000001", "index_name": "上证综指",
               "close": 3100.0, "pct_change": 0.5, "turnover": 3e11, "volume": 3e10,
               "source": "akshare", "fetched_at": "now"}
        self.store.upsert_index([row, dict(row, close=3200.0)])
        rows = self.store.index_for("20260716")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["close"], 3200.0)  # 覆盖为最新

    def test_sentiment_dedup(self):
        row = {"trade_date": "20260716", "up_count": 3000, "down_count": 1800,
               "limit_up_count": 40, "limit_down_count": 5, "up_down_ratio": 1.67,
               "total_turnover": 9e11, "margin_balance": 1.6e12, "fetched_at": "now"}
        self.store.upsert_sentiment(row)
        self.store.upsert_sentiment(dict(row, up_count=3500))
        got = self.store.sentiment_for("20260716")
        self.assertIsInstance(got, dict)
        self.assertEqual(got["up_count"], 3500)  # 覆盖为最新
        self.assertIsNone(self.store.sentiment_for("20991231"))

    def test_dragon_tiger_dedup(self):
        row = {"trade_date": "20260716", "stock_code": "000001", "stock_name": "平安",
               "reason": "日涨幅偏离", "net_buy": 1e7, "buy_amount": 2e7, "sell_amount": 1e7,
               "inst_seat_count": 2, "raw_json": "{}", "fetched_at": "now"}
        self.store.upsert_dragon_tiger([row, row])
        self.assertEqual(len(self.store.dragon_tiger_for("20260716")), 1)


if __name__ == "__main__":
    unittest.main()
