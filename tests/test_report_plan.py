"""report/plan.py 单测:决策矩阵(今天/历史/未来 × 交易日/时段/补拉成败 × brief/item)。

不联网:monkeypatch is_trading_day / _now_bj / _backfill / find.find_report;
今日库空,补拉成功=往临时库插今日 index 行来模拟。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from report import plan as plan_mod

_TODAY = dt.date.today()
_TODAY_S = _TODAY.strftime("%Y%m%d")
_YEST_S = (_TODAY - dt.timedelta(days=1)).strftime("%Y%m%d")

_INDEX_DDL = (
    "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, "
    "close REAL, pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
    "PRIMARY KEY(trade_date,index_code));"
)
_HOT_DDL = (
    "CREATE TABLE xq_hot_rank(date TEXT, symbol TEXT, name TEXT, rank INTEGER, "
    "hot_value REAL, market_type TEXT, PRIMARY KEY(date,symbol));"
)


def _insert_index(db: str, trade_date: str, fetched_at: str) -> None:
    c = sqlite3.connect(db)
    c.execute(_INDEX_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    c.executemany(
        "INSERT OR REPLACE INTO index_snapshot VALUES(?,?,?,?,?,?,?,?,?)",
        [
            (trade_date, "sh000001", "上证综指", 3100.0, 0.5, 3e11, 3e10, "akshare", fetched_at),
            (trade_date, "sz399001", "深证成指", 10000.0, -0.2, 2e11, 2e10, "akshare", fetched_at),
            (trade_date, "sz399006", "创业板指", 2000.0, 0.8, 1e11, 1e10, "akshare", fetched_at),
        ],
    )
    c.commit()
    c.close()


def _insert_hot(db: str, date_str: str) -> None:
    c = sqlite3.connect(db)
    c.execute(_HOT_DDL)
    c.execute("INSERT OR REPLACE INTO xq_hot_rank VALUES(?,?,?,?,?,?)",
              (date_str, "SH600000", "浦发银行", 1, 999.0, "沪深"))
    c.commit()
    c.close()


_SENT_DDL = (
    "CREATE TABLE IF NOT EXISTS market_sentiment(trade_date TEXT PRIMARY KEY, up_count INT, "
    "down_count INT, limit_up_count INT, limit_down_count INT, up_down_ratio REAL, "
    "total_turnover REAL, margin_balance REAL, fetched_at TEXT);"
)


def _insert_sentiment(db: str, trade_date: str, fetched_at: str) -> None:
    c = sqlite3.connect(db)
    c.execute(_SENT_DDL)
    c.execute("INSERT OR REPLACE INTO market_sentiment VALUES(?,?,?,?,?,?,?,?,?)",
              (trade_date, 3000, 1800, 40, 5, 1.67, 1.2e12, 1.8e12, fetched_at))
    c.commit()
    c.close()


class PlanBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "app.db")
        # 默认:PDF 不存在(避免 send 短路);交易日;已开盘 10:00;补拉不做任何事
        self._patch(plan_mod.find, "find_report",
                    lambda *a, **k: {"exists": False, "reusable": False, "path": None, "mtime_bj": None})
        self._patch(plan_mod, "is_trading_day", lambda d, *a: True)
        self._patch(plan_mod, "_now_bj", lambda: dt.datetime(_TODAY.year, _TODAY.month, _TODAY.day, 10, 0, tzinfo=plan_mod._BJ_TZ))
        self._patch(plan_mod, "_backfill", lambda *a, **k: None)

    def _patch(self, obj, name, val) -> None:
        old = getattr(obj, name)
        setattr(obj, name, val)
        self.addCleanup(lambda: setattr(obj, name, old))

    def _plan(self, date_symbol="today", kind="brief", groups=None):
        groups = groups or list(plan_mod.ALL_GROUPS)
        size = dict(plan_mod.SCENARIOS[plan_mod.DEFAULT_SCENARIO])
        return plan_mod.plan(date_symbol, kind, groups, size, self.db, "no-such.json", "18:10")


class TestFutureAndHistory(PlanBase):
    def test_future_date_message(self) -> None:
        fut = (_TODAY + dt.timedelta(days=3)).strftime("%Y%m%d")
        out = self._plan(date_symbol=fut)
        self.assertEqual(out["action"], "message")
        self.assertIn("未来日期", out["text"])

    def test_history_with_data_generates(self) -> None:
        _insert_index(self.db, _YEST_S, "2026-01-01T08:00:00+00:00")
        out = self._plan(date_symbol="yesterday", groups=["index"])
        self.assertEqual(out["action"], "generate")
        self.assertEqual(out["date"], _YEST_S)
        self.assertTrue(out["data"]["indexes"])

    def test_history_no_data_message(self) -> None:
        _insert_index(self.db, "20260731", "2026-01-01T08:00:00+00:00")  # 有更早的,供"最近有数据日"
        out = self._plan(date_symbol=_YEST_S, groups=["index"])
        self.assertEqual(out["action"], "message")
        self.assertIn("无数据", out["text"])
        self.assertIn("20260731", out["text"])

    def test_history_item_answers(self) -> None:
        _insert_index(self.db, _YEST_S, "2026-01-01T08:00:00+00:00")
        out = self._plan(date_symbol="yesterday", kind="item", groups=["index"])
        self.assertEqual(out["action"], "answer")

    def test_history_reusable_pdf_sends(self) -> None:
        self._patch(plan_mod.find, "find_report",
                    lambda *a, **k: {"exists": True, "reusable": True, "path": "/x/y.pdf", "mtime_bj": "x"})
        out = self._plan(date_symbol="yesterday", groups=["index"])
        self.assertEqual(out["action"], "send")
        self.assertEqual(out["path"], "/x/y.pdf")


class TestTodayTiming(PlanBase):
    def test_non_trading_day_closed(self) -> None:
        self._patch(plan_mod, "is_trading_day", lambda d, *a: False)
        out = self._plan(groups=["index"])
        self.assertEqual(out["text"], plan_mod.MSG_CLOSED)

    def test_pre_open_message(self) -> None:
        self._patch(plan_mod, "_now_bj",
                    lambda: dt.datetime(_TODAY.year, _TODAY.month, _TODAY.day, 8, 58, tzinfo=plan_mod._BJ_TZ))
        out = self._plan(groups=["index"])
        self.assertEqual(out["text"], plan_mod.MSG_PREOPEN)

    def test_backfill_success_generates_intraday(self) -> None:
        # 补拉 = 往库里插今日 index(fetched_at 北京时 10:00 < 15:35 → 盘中)
        self._patch(plan_mod, "_backfill",
                    lambda *a, **k: _insert_index(self.db, _TODAY_S, f"{_TODAY_S[:4]}-{_TODAY_S[4:6]}-{_TODAY_S[6:]}T02:00:00+00:00"))
        out = self._plan(groups=["index"])
        self.assertEqual(out["action"], "generate")
        self.assertEqual(out["date"], _TODAY_S)
        self.assertTrue(out["intraday"])
        self.assertTrue(out["data"]["indexes"])

    def test_backfill_success_after_close_not_intraday(self) -> None:
        # fetched_at 北京时 16:00 (UTC 08:00) >= 15:35 → 收盘数据,intraday False
        self._patch(plan_mod, "_backfill",
                    lambda *a, **k: _insert_index(self.db, _TODAY_S, f"{_TODAY_S[:4]}-{_TODAY_S[4:6]}-{_TODAY_S[6:]}T08:00:00+00:00"))
        out = self._plan(groups=["index"])
        self.assertEqual(out["action"], "generate")
        self.assertFalse(out["intraday"])

    def test_backfill_fail_message(self) -> None:
        # 补拉什么都不做 → 库里仍无今日 index → 拉取失败
        out = self._plan(groups=["index"])
        self.assertEqual(out["text"], plan_mod.MSG_FETCH_FAIL)

    def test_today_reusable_pdf_sends_before_timing(self) -> None:
        self._patch(plan_mod.find, "find_report",
                    lambda *a, **k: {"exists": True, "reusable": True, "path": "/a/b.pdf", "mtime_bj": "x"})
        self._patch(plan_mod, "is_trading_day", lambda d, *a: (_ for _ in ()).throw(AssertionError("不该判交易日")))
        out = self._plan(groups=["index"])
        self.assertEqual(out["action"], "send")

    def test_sentiment_only_item_backfill_success(self) -> None:
        # 只请求 sentiment(data 里没有 indexes 键)——补拉到情绪就该 answer,不能误判拉取失败
        self._patch(plan_mod, "_backfill",
                    lambda *a, **k: _insert_sentiment(self.db, _TODAY_S, f"{_TODAY_S[:4]}-{_TODAY_S[4:6]}-{_TODAY_S[6:]}T08:00:00+00:00"))
        out = self._plan(kind="item", groups=["sentiment"])
        self.assertEqual(out["action"], "answer")
        self.assertTrue(out["data"]["sentiment"])

    def test_sentiment_only_item_backfill_fail(self) -> None:
        out = self._plan(kind="item", groups=["sentiment"])
        self.assertEqual(out["text"], plan_mod.MSG_FETCH_FAIL)


class TestTodayNonMarketItem(PlanBase):
    def test_hot_rank_item_answers_without_timing(self) -> None:
        # 纯雪球单项:不判时段、不补拉。故意把 is_trading_day/_backfill 设成一调用就失败
        self._patch(plan_mod, "is_trading_day", lambda d, *a: (_ for _ in ()).throw(AssertionError("不该判交易日")))
        self._patch(plan_mod, "_backfill", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该补拉")))
        _insert_hot(self.db, _TODAY_S)
        out = self._plan(kind="item", groups=["hot_rank"])
        self.assertEqual(out["action"], "answer")
        self.assertTrue(out["data"]["xq_hot_rank_today"])

    def test_non_market_item_empty_message(self) -> None:
        self._patch(plan_mod, "is_trading_day", lambda d, *a: (_ for _ in ()).throw(AssertionError("不该判交易日")))
        out = self._plan(kind="item", groups=["hot_rank"])
        self.assertEqual(out["action"], "message")
        self.assertIn("暂无数据", out["text"])


class TestIsTradingDay(unittest.TestCase):
    """交易日历:文件缓存命中 / 拉取写缓存 / akshare 挂用过期缓存 / 无缓存周末启发式。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = str(Path(self.tmp.name) / ".trade_cal.json")

    def _patch_fetch(self, fn) -> None:
        old = plan_mod._fetch_trade_days
        plan_mod._fetch_trade_days = fn
        self.addCleanup(lambda: setattr(plan_mod, "_fetch_trade_days", old))

    def test_fresh_cache_used_without_fetch(self) -> None:
        Path(self.cache).write_text(json.dumps([_TODAY.isoformat()]), encoding="utf-8")
        self._patch_fetch(lambda: (_ for _ in ()).throw(AssertionError("有新鲜缓存不该 fetch")))
        self.assertTrue(plan_mod.is_trading_day(_TODAY, self.cache))

    def test_fetch_writes_cache(self) -> None:
        self._patch_fetch(lambda: {_TODAY.isoformat()})
        self.assertTrue(plan_mod.is_trading_day(_TODAY, self.cache))
        self.assertIn(_TODAY.isoformat(), json.loads(Path(self.cache).read_text(encoding="utf-8")))

    def test_fetch_fail_uses_stale_cache(self) -> None:
        Path(self.cache).write_text(json.dumps([_TODAY.isoformat()]), encoding="utf-8")
        os.utime(self.cache, (0, 0))  # mtime=1970 → 过 TTL
        self._patch_fetch(lambda: (_ for _ in ()).throw(RuntimeError("akshare down")))
        self.assertTrue(plan_mod.is_trading_day(_TODAY, self.cache))  # 用过期缓存兜住

    def test_fetch_fail_no_cache_weekday_heuristic(self) -> None:
        self._patch_fetch(lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertTrue(plan_mod.is_trading_day(dt.date(2026, 9, 7), self.cache))   # 周一
        self.assertFalse(plan_mod.is_trading_day(dt.date(2026, 9, 5), self.cache))  # 周六



if __name__ == "__main__":
    unittest.main()
