"""雪球抓取层纯函数单测:时间戳换算、slim_xq_post 裁剪、select_xq_comments 过滤。"""
from __future__ import annotations

import unittest

from xueqiu.crawler import (
    XueqiuClient,
    slim_hot_rank,
    slim_livenews,
    slim_xq_post,
    select_xq_comments,
    xq_ms_to_epoch,
)


class TestXueqiuPure(unittest.TestCase):
    def test_ms_to_epoch(self):
        self.assertEqual(xq_ms_to_epoch(1700000000000), 1700000000)
        self.assertIsNone(xq_ms_to_epoch(None))
        self.assertIsNone(xq_ms_to_epoch("bad"))

    def test_slim_post_basic(self):
        s = {
            "id": 42, "user": {"screen_name": "王五", "id": 9},
            "created_at": 1700000000000, "description": "<p>看多 <a>网页链接</a></p>",
            "source": "雪球", "retweet_count": 1, "reply_count": 2, "fav_count": 3,
        }
        row = slim_xq_post("9", s)
        self.assertEqual(row["id"], "42")
        self.assertEqual(row["user_name"], "王五")
        self.assertEqual(row["created_ts"], 1700000000)
        self.assertIn("看多", row["text"])
        self.assertNotIn("<p>", row["text"])
        self.assertEqual(row["like_count"], 3)

    def test_select_comments_author_only(self):
        author = "9"
        comments = [
            {"id": 1, "user": {"id": 9}, "text": "作者一级评论"},
            {"id": 2, "user": {"id": 7}, "text": "路人评论", "comments": [
                {"id": 21, "user": {"id": 9}, "text": "作者的回复"},
            ]},
            {"id": 3, "user": {"id": 5}, "text": "无关路人"},
        ]
        rows = select_xq_comments(author, "42", comments, "author_only")
        ids = {r["id"] for r in rows}
        self.assertIn("1", ids)   # 作者一级评论
        self.assertIn("2", ids)   # 路人父评论(作为作者回复的上下文)
        self.assertIn("21", ids)  # 作者的回复
        self.assertNotIn("3", ids)  # 无关路人应被过滤


class TestSlimHotRank(unittest.TestCase):
    def test_rank_from_index_and_fields(self):
        items = [
            {"code": "SH600000", "name": "浦发银行", "value": 12345.0},
            {"code": "SZ000001", "name": "平安银行", "value": 6789.0},
        ]
        rows = slim_hot_rank(items, "沪深", "20260717")
        self.assertEqual(rows[0]["symbol"], "SH600000")
        self.assertEqual(rows[0]["rank"], 1)          # 下标 +1
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[0]["hot_value"], 12345.0)
        self.assertEqual(rows[0]["market_type"], "沪深")
        self.assertEqual(rows[0]["date"], "20260717")

    def test_skip_items_without_code(self):
        rows = slim_hot_rank([{"name": "无代码"}], "沪深", "20260717")
        self.assertEqual(rows, [])


class TestSlimLivenews(unittest.TestCase):
    def test_html_stripped_and_ts(self):
        it = {"id": 77, "created_at": 1700000000000, "text": "<p>央行降准 <a>网页链接</a></p>"}
        row = slim_livenews(it)
        self.assertEqual(row["id"], "77")
        self.assertEqual(row["created_ts"], 1700000000)
        self.assertIn("央行降准", row["text"])
        self.assertNotIn("<p>", row["text"])

    def test_uses_description_when_no_text(self):
        row = slim_livenews({"id": 1, "created_at": 1700000000000, "description": "<b>公告</b>"})
        self.assertIn("公告", row["text"])
        self.assertNotIn("<b>", row["text"])


class _StubClient(XueqiuClient):
    """构造真实 client(不发网络),把 _get_json 换成返回预置 JSON。"""
    def __init__(self, canned: dict):
        super().__init__("k=v", 0.0, 0.0)
        self._canned = canned

    def _get_json(self, url, params, referer=None):
        return self._canned


class TestFetchParsing(unittest.TestCase):
    def test_fetch_hot_rank_extracts_items(self):
        client = _StubClient({"data": {"items": [{"code": "SH600000", "name": "浦发", "value": 1.0}]}})
        items = client.fetch_hot_rank(size=10, market_type="沪深")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "SH600000")

    def test_fetch_livenews_returns_rows_and_cursor(self):
        client = _StubClient({
            "items": [{"id": 5, "created_at": 1700000000000, "text": "<p>快讯</p>"}],
            "next_max_id": 4,
        })
        rows, next_max_id = client.fetch_livenews(max_id=-1, count=15)
        self.assertEqual(rows[0]["id"], "5")
        self.assertNotIn("<p>", rows[0]["text"])
        self.assertEqual(next_max_id, 4)


class TestBootstrap(unittest.TestCase):
    def test_anonymous_bootstraps_when_no_token(self):
        # 不传 cookie = 匿名模式:首次应无 token,bootstrap 后种上 xq_a_token
        client = XueqiuClient(min_delay=0.0, max_delay=0.0)
        calls: list[str] = []

        def fake_get(url, **kw):
            calls.append(url)
            client.session.cookies.set("xq_a_token", "tok", domain=".xueqiu.com")
            return object()

        client.session.get = fake_get
        self.assertFalse(client._has_token())
        self.assertTrue(client._bootstrap_token())
        self.assertTrue(client._has_token())
        self.assertIn("https://xueqiu.com/hq", calls)

    def test_login_cookie_has_token_no_bootstrap(self):
        # 传登录 cookie(含 xq_a_token)= 登录模式:已有 token,无需 bootstrap
        client = XueqiuClient("xq_a_token=abc; u=1", 0.0, 0.0)
        self.assertTrue(client._has_token())


if __name__ == "__main__":
    unittest.main()
