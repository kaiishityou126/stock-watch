"""简报数据读取单测:seed 一个库,验证按 lookback 取当天文本 + 当天行情快照。"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from report.reader import _config_db_path, _stale_today, collect


def _today_str() -> str:
    return dt.date.today().strftime("%Y%m%d")


def _make_market_db(path: str, fetched_at: str) -> None:
    """建一个只含 index_snapshot 的库,插入今天的一行指数,便于测 is_close_data。"""
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, close REAL, "
        "pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
        "PRIMARY KEY(trade_date,index_code));"
    )
    c.execute(
        "INSERT INTO index_snapshot VALUES(?, 'sh000001','上证综指',3100.0,0.5,3e11,3e10,'akshare',?)",
        (_today_str(), fetched_at),
    )
    c.commit()
    c.close()


class TestReportReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        now = int(time.time())
        old = now - 3 * 86400
        c = sqlite3.connect(self.db)
        c.executescript(
            "CREATE TABLE posts(id TEXT PRIMARY KEY, uid TEXT, user_name TEXT, created_ts INTEGER, text TEXT);"
            "CREATE TABLE xq_posts(id TEXT PRIMARY KEY, user_id TEXT, user_name TEXT, created_ts INTEGER, text TEXT);"
            "CREATE TABLE market_snapshot(trade_date TEXT, sector TEXT, sector_type TEXT DEFAULT '行业', "
            "pct_change REAL, turnover REAL, volume REAL, super_large_net REAL, large_net REAL, "
            "medium_net REAL, small_net REAL, main_net_inflow_est REAL, retail_net_inflow_est REAL, "
            "source TEXT, fetched_at TEXT, PRIMARY KEY(trade_date,sector,sector_type));"
            "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, close REAL, "
            "pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
            "PRIMARY KEY(trade_date,index_code));"
            "CREATE TABLE market_sentiment(trade_date TEXT PRIMARY KEY, up_count INTEGER, "
            "down_count INTEGER, limit_up_count INTEGER, limit_down_count INTEGER, up_down_ratio REAL, "
            "total_turnover REAL, margin_balance REAL, fetched_at TEXT);"
            "CREATE TABLE dragon_tiger(trade_date TEXT, stock_code TEXT, stock_name TEXT, reason TEXT, "
            "net_buy REAL, buy_amount REAL, sell_amount REAL, inst_seat_count INTEGER, raw_json TEXT, "
            "fetched_at TEXT, PRIMARY KEY(trade_date,stock_code,reason));"
        )
        c.execute("INSERT INTO posts VALUES('1','u','大V',?, '看多半导体')", (now,))
        c.execute("INSERT INTO posts VALUES('2','u','大V',?, '这是三天前')", (old,))
        c.execute("INSERT INTO xq_posts VALUES('9','x','雪球客',?, '新能源要涨')", (now,))
        c.execute("INSERT INTO market_snapshot(trade_date,sector,sector_type,pct_change,turnover,"
                  "volume,main_net_inflow_est,retail_net_inflow_est,source,fetched_at) "
                  "VALUES('20260716','半导体','行业',2.1,1.0,1.0,1e8,-2e7,'akshare','now')")
        c.execute("INSERT INTO index_snapshot VALUES('20260716','sh000001','上证综指',3100.0,0.5,3e11,3e10,'akshare','now')")
        c.execute("INSERT INTO market_sentiment VALUES('20260716',3000,1500,42,6,2.0,9e11,1.6e12,'now')")
        c.executescript(
            "CREATE TABLE xq_hot_rank(date TEXT, symbol TEXT, name TEXT, rank INTEGER, "
            "hot_value REAL, market_type TEXT, fetched_at TEXT, PRIMARY KEY(date,symbol));"
            "CREATE TABLE xq_livenews(id TEXT PRIMARY KEY, created_ts INTEGER, created_at TEXT, "
            "text TEXT, raw_json TEXT, fetched_at TEXT);"
        )
        # 两日热榜:20260716(前一日) + 20260717(今日,= trade_date)
        c.execute("INSERT INTO xq_hot_rank VALUES('20260716','SH600000','浦发',3,10.0,'沪深','now')")
        c.execute("INSERT INTO xq_hot_rank VALUES('20260717','SH600000','浦发',1,20.0,'沪深','now')")
        c.execute("INSERT INTO xq_hot_rank VALUES('20260717','SZ300750','宁德',2,15.0,'沪深','now')")
        c.execute("INSERT INTO xq_livenews VALUES('n1',?, '', '宁德时代扩产', '{}', 'now')", (now,))
        c.execute("INSERT INTO xq_livenews VALUES('n2',?, '', '三天前旧闻', '{}', 'now')", (old,))
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect_respects_lookback(self):
        data = collect(self.db, lookback_hours=24, trade_date="20260716")
        wb_texts = [p["text"] for p in data["weibo_posts"]]
        self.assertIn("看多半导体", wb_texts)
        self.assertNotIn("这是三天前", wb_texts)     # 超出 lookback
        self.assertEqual(len(data["xq_posts"]), 1)
        self.assertEqual(len(data["sectors"]), 1)
        self.assertEqual(data["sectors"][0]["sector_type"], "行业")
        self.assertEqual(len(data["indexes"]), 1)
        self.assertEqual(data["indexes"][0]["index_code"], "sh000001")
        self.assertIsInstance(data["sentiment"], dict)
        self.assertEqual(data["sentiment"]["up_count"], 3000)
        self.assertEqual(data["date"], "20260716")

    def test_collect_reads_hot_rank_two_days_and_news(self):
        data = collect(self.db, lookback_hours=24, trade_date="20260717")
        today = {r["symbol"]: r["rank"] for r in data["xq_hot_rank_today"]}
        prev = {r["symbol"]: r["rank"] for r in data["xq_hot_rank_prev"]}
        self.assertEqual(today["SH600000"], 1)
        self.assertEqual(today["SZ300750"], 2)
        self.assertEqual(prev["SH600000"], 3)          # 前一日
        self.assertNotIn("SZ300750", prev)             # 前一日无宁德
        news_texts = [n["text"] for n in data["xq_livenews"]]
        self.assertIn("宁德时代扩产", news_texts)
        self.assertNotIn("三天前旧闻", news_texts)      # 超 lookback

    def test_collect_tolerates_missing_tables(self):
        # 只用微博、从没跑过雪球/行情时,对应表不存在也不应崩,返回空
        db = str(Path(self.tmp.name) / "only_posts.db")
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE posts(id TEXT PRIMARY KEY, uid TEXT, user_name TEXT, created_ts INTEGER, text TEXT);"
        )
        c.execute("INSERT INTO posts VALUES('1','u','大V',?, '看多半导体')", (int(time.time()),))
        c.commit()
        c.close()
        data = collect(db, lookback_hours=24, trade_date="20260716")
        self.assertEqual(len(data["weibo_posts"]), 1)
        self.assertEqual(data["xq_posts"], [])
        self.assertEqual(data["sectors"], [])
        self.assertEqual(data["indexes"], [])
        self.assertIsNone(data["sentiment"])
        self.assertEqual(data["dragon_tiger"], [])
        self.assertEqual(data["xq_hot_rank_today"], [])
        self.assertEqual(data["xq_livenews"], [])

    def test_is_close_data_historical_date_true(self):
        # 历史日期已过完,库里是完整收盘数据,恒判 True
        data = collect(self.db, lookback_hours=24, trade_date="20260716")
        self.assertTrue(data["is_close_data"])

    def test_is_close_data_today_intraday_false(self):
        # 今天盘中(UTC 02:00 = 北京 10:00)拉的数据 → 判为盘中快照,带拉取时刻
        db = str(Path(self.tmp.name) / "today_intraday.db")
        _make_market_db(db, "2026-01-05T02:00:00+00:00")
        data = collect(db, lookback_hours=24, trade_date=_today_str(), groups=["index"])
        self.assertFalse(data["is_close_data"])
        self.assertEqual(data["fetched_at_bj"], "10:00")

    def test_is_close_data_today_after_close_true(self):
        # 今天 15:35 后(UTC 08:00 = 北京 16:00)拉的数据 → 判为收盘数据
        db = str(Path(self.tmp.name) / "today_close.db")
        _make_market_db(db, "2026-01-05T08:00:00+00:00")
        data = collect(db, lookback_hours=24, trade_date=_today_str(), groups=["index"])
        self.assertTrue(data["is_close_data"])

    def test_stale_today_auto_path_marks_old_date(self):
        # 自动取日期 + 请求行情腿 + 库里只有旧日期 → is_today_data false 信号
        today = _today_str()
        yest = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
        self.assertTrue(_stale_today({"date": yest}, ["index"], True))
        self.assertTrue(_stale_today({"date": None}, ["index"], True))      # 库里完全没数据
        self.assertFalse(_stale_today({"date": today}, ["index"], True))    # 今天已有数据
        self.assertFalse(_stale_today({"date": yest}, ["hot_rank"], True))  # 没请求行情腿
        self.assertFalse(_stale_today({"date": yest}, ["index"], False))    # 点名历史日期,不标记

    def test_main_auto_path_emits_stale_marker(self):
        # CLI 集成:自动取日期时库里只有旧日期 → stdout 带 is_today_data:false
        import contextlib
        import io
        import json as _json

        from report import reader as reader_mod

        db = str(Path(self.tmp.name) / "stale.db")
        yest = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, close REAL, "
            "pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
            "PRIMARY KEY(trade_date,index_code));"
        )
        c.execute(
            "INSERT INTO index_snapshot VALUES(?, 'sh000001','上证综指',3100.0,0.5,3e11,3e10,"
            "'akshare','2026-01-01T08:00:00+00:00')",
            (yest,),
        )
        c.commit()
        c.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = reader_mod.main(["--db", db, "--groups", "index"])
        self.assertEqual(rc, 0)
        out = _json.loads(buf.getvalue())
        self.assertIs(out.get("is_today_data"), False)
        self.assertEqual(out["date"], yest)      # 回退到旧日期,但打了信号

    def test_main_explicit_date_no_data_errors(self):
        # 点名具体日期但库里无该日数据 → 报 error(不静默输出空,含最近有数据日)
        import contextlib
        import io
        import json as _json

        from report import reader as reader_mod

        db = str(Path(self.tmp.name) / "nodata.db")
        old3 = (dt.date.today() - dt.timedelta(days=3)).strftime("%Y%m%d")
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, close REAL, "
            "pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
            "PRIMARY KEY(trade_date,index_code));"
        )
        c.execute(
            "INSERT INTO index_snapshot VALUES(?, 'sh000001','上证综指',3100.0,0.5,3e11,3e10,"
            "'akshare','2026-01-01T08:00:00+00:00')",
            (old3,),
        )
        c.commit()
        c.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = reader_mod.main(["--db", db, "--groups", "index", "--date", "yesterday"])
        self.assertEqual(rc, 0)
        out = _json.loads(buf.getvalue())
        self.assertIn("无数据", out["error"])

    def test_config_db_path_reads_config(self):
        cfg = str(Path(self.tmp.name) / "config.json")
        Path(cfg).write_text('{"db_path": "C:/x/app.db"}', encoding="utf-8")
        self.assertEqual(_config_db_path(cfg), "C:/x/app.db")

    def test_config_db_path_missing_falls_back_none(self):
        # config 无 db_path / 文件不存在 → None,由 CLI 回退默认 data/app.db
        cfg = str(Path(self.tmp.name) / "config_no_key.json")
        Path(cfg).write_text('{"bloggers": []}', encoding="utf-8")
        self.assertIsNone(_config_db_path(cfg))
        self.assertIsNone(_config_db_path(str(Path(self.tmp.name) / "nope.json")))

    def test_is_close_data_today_no_market_data_false(self):
        # 请求了行情腿但今天全空(交易日盘中还没拉过)→ is_close_data False,触发按需补拉
        db = str(Path(self.tmp.name) / "today_empty.db")
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE index_snapshot(trade_date TEXT, index_code TEXT, index_name TEXT, close REAL, "
            "pct_change REAL, turnover REAL, volume REAL, source TEXT, fetched_at TEXT, "
            "PRIMARY KEY(trade_date,index_code));"
        )
        c.commit()
        c.close()
        data = collect(db, lookback_hours=24, trade_date=_today_str(), groups=["index"])
        self.assertFalse(data["is_close_data"])
        self.assertNotIn("fetched_at_bj", data)


if __name__ == "__main__":
    unittest.main()
