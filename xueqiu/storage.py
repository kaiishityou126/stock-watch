"""雪球 SQLite 存储层(与微博共用 data/app.db,表名加 xq_ 前缀)。

三张表,结构对齐微博:
  xq_posts       博文(id 主键 → 去重;comments_done 标记评论是否抓过)
  xq_comments    评论(id 主键;parent_id 区分一级/二级)
  xq_crawl_state 每个博主状态(增量水位 last_ts + 全量断点 full_page/full_done)

雪球 created_at 是毫秒时间戳,增量水位用 last_ts(已见过的最大 created_ts,epoch 秒)。
CREATE TABLE IF NOT EXISTS 幂等:与微博 Storage 各自开连接同库互不干扰。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS xq_posts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    user_name     TEXT,
    created_at    TEXT,
    created_ts    INTEGER,
    text          TEXT,
    source        TEXT,
    retweet_count INTEGER,
    reply_count   INTEGER,
    like_count    INTEGER,
    is_long_text  INTEGER DEFAULT 0,
    pic_urls      TEXT,
    retweeted_id  TEXT,
    retweeted_text TEXT,
    comments_done INTEGER DEFAULT 0,
    raw_json      TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_posts_user_ts ON xq_posts(user_id, created_ts DESC);

CREATE TABLE IF NOT EXISTS xq_comments (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    parent_id   TEXT,
    created_at  TEXT,
    text        TEXT,
    like_counts INTEGER,
    user_name   TEXT,
    user_id     TEXT,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_comments_post ON xq_comments(post_id);

CREATE TABLE IF NOT EXISTS xq_crawl_state (
    user_id    TEXT PRIMARY KEY,
    last_ts    INTEGER DEFAULT 0,
    full_page  INTEGER DEFAULT 0,
    full_done  INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS xq_hot_rank (
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    name        TEXT,
    rank        INTEGER,
    hot_value   REAL,
    market_type TEXT,
    fetched_at  TEXT,
    PRIMARY KEY(date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_xq_hot_rank_symbol_date ON xq_hot_rank(symbol, date);

CREATE TABLE IF NOT EXISTS xq_livenews (
    id         TEXT PRIMARY KEY,
    created_ts INTEGER,
    created_at TEXT,
    text       TEXT,
    raw_json   TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_livenews_ts ON xq_livenews(created_ts DESC);
"""


class XueqiuStorage:
    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- 状态 ----
    def get_last_ts(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT last_ts FROM xq_crawl_state WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["last_ts"]) if row else 0

    def set_last_ts(self, user_id: str, value: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO xq_crawl_state(user_id, last_ts, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_ts=excluded.last_ts, "
                "updated_at=excluded.updated_at",
                (user_id, value, now_iso),
            )

    def get_full_progress(self, user_id: str) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT full_page, full_done FROM xq_crawl_state WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return 0, 0
        return int(row["full_page"] or 0), int(row["full_done"] or 0)

    def set_full_progress(self, user_id: str, page: int, done: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO xq_crawl_state(user_id, full_page, full_done, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET full_page=excluded.full_page, "
                "full_done=excluded.full_done, updated_at=excluded.updated_at",
                (user_id, page, done, now_iso),
            )

    # ---- 博文 ----
    def upsert_posts(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO xq_posts (id, user_id, user_name, created_at, created_ts, text, "
                "source, retweet_count, reply_count, like_count, is_long_text, pic_urls, "
                "retweeted_id, retweeted_text, raw_json, fetched_at) VALUES "
                "(:id,:user_id,:user_name,:created_at,:created_ts,:text,:source,:retweet_count,"
                ":reply_count,:like_count,:is_long_text,:pic_urls,:retweeted_id,:retweeted_text,"
                ":raw_json,:fetched_at) "
                "ON CONFLICT(id) DO UPDATE SET user_name=excluded.user_name, "
                "created_at=excluded.created_at, created_ts=excluded.created_ts, text=excluded.text, "
                "source=excluded.source, retweet_count=excluded.retweet_count, "
                "reply_count=excluded.reply_count, like_count=excluded.like_count, "
                "is_long_text=excluded.is_long_text, pic_urls=excluded.pic_urls, "
                "retweeted_id=excluded.retweeted_id, retweeted_text=excluded.retweeted_text, "
                "raw_json=excluded.raw_json, fetched_at=excluded.fetched_at",
                rows,
            )

    def posts_pending_comments(self, user_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM xq_posts WHERE user_id = ? AND comments_done = 0 AND reply_count > 0 "
            "ORDER BY created_ts DESC",
            (user_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def recent_post_ids(self, user_id: str, cutoff_ts: float) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM xq_posts WHERE user_id = ? AND created_ts >= ? AND reply_count > 0",
            (user_id, cutoff_ts),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_comments_done(self, post_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE xq_posts SET comments_done = 1 WHERE id = ?", (post_id,))

    # ---- 评论 ----
    def upsert_comments(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO xq_comments (id, post_id, parent_id, created_at, text, "
                "like_counts, user_name, user_id, fetched_at) VALUES "
                "(:id,:post_id,:parent_id,:created_at,:text,:like_counts,:user_name,:user_id,:fetched_at)",
                rows,
            )

    # ---- 统计 ----
    def count_posts(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_posts").fetchone()["n"]

    def count_comments(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_comments").fetchone()["n"]

    # ---- 热股榜(每日快照,(date,symbol) 幂等)----
    def upsert_hot_rank(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO xq_hot_rank (date, symbol, name, rank, hot_value, market_type, fetched_at) "
                "VALUES (:date,:symbol,:name,:rank,:hot_value,:market_type,:fetched_at) "
                "ON CONFLICT(date, symbol) DO UPDATE SET name=excluded.name, rank=excluded.rank, "
                "hot_value=excluded.hot_value, market_type=excluded.market_type, "
                "fetched_at=excluded.fetched_at",
                rows,
            )

    def count_hot_rank(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_hot_rank").fetchone()["n"]

    # ---- 7x24 快讯(id 去重)----
    def insert_livenews(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR IGNORE INTO xq_livenews (id, created_ts, created_at, text, raw_json, fetched_at) "
                "VALUES (:id,:created_ts,:created_at,:text,:raw_json,:fetched_at)",
                rows,
            )

    def livenews_exists(self, news_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM xq_livenews WHERE id = ?", (news_id,)
        ).fetchone() is not None

    def count_livenews(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_livenews").fetchone()["n"]
