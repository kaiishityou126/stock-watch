"""雪球存储层单测:临时 SQLite 验证 id 去重、增量水位、断点续抓。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xueqiu.storage import XueqiuStorage


def _post(pid: str, ts: int) -> dict:
    return {
        "id": pid, "user_id": "u1", "user_name": "张三", "created_at": "",
        "created_ts": ts, "text": "看好新能源", "source": "雪球",
        "retweet_count": 0, "reply_count": 1, "like_count": 2,
        "is_long_text": 0, "pic_urls": "[]", "retweeted_id": "",
        "retweeted_text": "", "raw_json": "{}", "fetched_at": "now",
    }


class TestXueqiuStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        self.store = XueqiuStorage(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_upsert_dedup(self):
        self.store.upsert_posts([_post("1", 100), _post("1", 100)])
        self.assertEqual(self.store.count_posts(), 1)

    def test_last_ts_roundtrip(self):
        self.assertEqual(self.store.get_last_ts("u1"), 0)
        self.store.set_last_ts("u1", 12345, "now")
        self.assertEqual(self.store.get_last_ts("u1"), 12345)

    def test_full_progress_roundtrip(self):
        self.assertEqual(self.store.get_full_progress("u1"), (0, 0))
        self.store.set_full_progress("u1", 3, 0, "now")
        self.assertEqual(self.store.get_full_progress("u1"), (3, 0))

    def test_comment_dedup(self):
        row = {"id": "c1", "post_id": "1", "parent_id": None, "created_at": "",
               "text": "同意", "like_counts": 0, "user_name": "李四",
               "user_id": "u2", "fetched_at": "now"}
        self.store.upsert_comments([row, row])
        self.assertEqual(self.store.count_comments(), 1)


def _hot(date: str, symbol: str, rank: int, hot: float = 1.0) -> dict:
    return {
        "date": date, "symbol": symbol, "name": symbol + "名",
        "rank": rank, "hot_value": hot, "market_type": "沪深", "fetched_at": "now",
    }


def _news(nid: str, ts: int) -> dict:
    return {
        "id": nid, "created_ts": ts, "created_at": "", "text": "利好消息",
        "raw_json": "{}", "fetched_at": "now",
    }


class TestXueqiuAttentionStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        self.store = XueqiuStorage(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_hot_rank_same_day_idempotent(self):
        # 同 (date,symbol) 重复跑:只更新不新增,rank 取最新值
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 5)])
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 2)])
        self.assertEqual(self.store.count_hot_rank(), 1)
        row = self.store.conn.execute(
            "SELECT rank FROM xq_hot_rank WHERE date=? AND symbol=?", ("20260717", "SH600000")
        ).fetchone()
        self.assertEqual(row["rank"], 2)

    def test_hot_rank_multi_day_kept(self):
        # 不同日期同一只票 → 两行(时间序列)
        self.store.upsert_hot_rank([_hot("20260716", "SH600000", 3)])
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 1)])
        self.assertEqual(self.store.count_hot_rank(), 2)

    def test_livenews_id_dedup(self):
        self.store.insert_livenews([_news("n1", 100), _news("n1", 100)])
        self.assertEqual(self.store.count_livenews(), 1)

    def test_livenews_exists(self):
        self.assertFalse(self.store.livenews_exists("n1"))
        self.store.insert_livenews([_news("n1", 100)])
        self.assertTrue(self.store.livenews_exists("n1"))


if __name__ == "__main__":
    unittest.main()
