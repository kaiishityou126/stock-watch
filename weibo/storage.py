"""SQLite 存储层。

三张表:
  posts       博文(id 主键 → 天然去重;comments_done 标记评论是否抓过)
  comments    评论(id 主键;parent_id 区分一级/二级)
  crawl_state 每个博主的状态(增量 last_max_id + 全量断点 full_since_id/full_done)

去重:comments 用 INSERT OR REPLACE 覆盖(赞数会变);posts 用 ON CONFLICT DO UPDATE
只更新内容列,保留 comments_done(否则重列会把断点标记清零)。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    uid           TEXT NOT NULL,
    bid           TEXT,           -- 微博 base62 短 id(m 站 mblog.bid)
    user_name     TEXT,           -- 博主昵称,分析时"谁说的"更直观
    created_at    TEXT,
    created_ts    INTEGER,        -- 解析后的 epoch 秒,便于按时间查询
    text          TEXT,           -- 纯文本正文(长文已补全)
    source        TEXT,
    region_name   TEXT,
    reposts_count INTEGER,
    comments_count INTEGER,
    attitudes_count INTEGER,
    is_top        INTEGER DEFAULT 0,
    is_long_text  INTEGER DEFAULT 0,
    pic_urls      TEXT,           -- 配图原图 URL 的 JSON 数组(供后续 OCR)
    retweeted_id  TEXT,           -- 转发的原博 id(非转发为空)
    retweeted_text TEXT,          -- 转发原博正文(纯文本)
    comments_done INTEGER DEFAULT 0, -- 该博文评论是否已抓(全量断点续抓用)
    raw_json      TEXT,           -- 原始 mblog JSON 兜底,清洗规则改了能重放
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_uid_ts ON posts(uid, created_ts DESC);

CREATE TABLE IF NOT EXISTS comments (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    parent_id   TEXT,             -- NULL=一级评论;否则为所属一级评论 id
    created_at  TEXT,
    text        TEXT,
    like_counts INTEGER,
    user_name   TEXT,
    user_id     TEXT,
    source      TEXT,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);

CREATE TABLE IF NOT EXISTS crawl_state (
    uid           TEXT PRIMARY KEY,
    last_max_id   INTEGER DEFAULT 0,
    full_since_id TEXT DEFAULT '',   -- 全量翻页游标断点(续抓用)
    full_done     INTEGER DEFAULT 0, -- 全量是否已翻到底
    updated_at    TEXT
);
"""


class Storage:
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
    def get_last_max_id(self, uid: str) -> int:
        row = self.conn.execute(
            "SELECT last_max_id FROM crawl_state WHERE uid = ?", (uid,)
        ).fetchone()
        return int(row["last_max_id"]) if row else 0

    def set_last_max_id(self, uid: str, value: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO crawl_state(uid, last_max_id, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET last_max_id=excluded.last_max_id, "
                "updated_at=excluded.updated_at",
                (uid, value, now_iso),
            )

    def get_full_progress(self, uid: str) -> tuple[str, int]:
        """返回 (full_since_id, full_done)。无记录返回 ('', 0)。"""
        row = self.conn.execute(
            "SELECT full_since_id, full_done FROM crawl_state WHERE uid = ?", (uid,)
        ).fetchone()
        if not row:
            return "", 0
        return row["full_since_id"] or "", int(row["full_done"] or 0)

    def set_full_progress(self, uid: str, since_id: str, done: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO crawl_state(uid, full_since_id, full_done, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET full_since_id=excluded.full_since_id, "
                "full_done=excluded.full_done, updated_at=excluded.updated_at",
                (uid, since_id, done, now_iso),
            )

    # ---- 博文 ----
    def upsert_posts(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            # 用 ON CONFLICT DO UPDATE 而非 INSERT OR REPLACE:
            # 后者会删行重插,把 comments_done 清零 → 断点失效。这里只更新内容列,保留 comments_done。
            c.executemany(
                "INSERT INTO posts (id, uid, bid, user_name, created_at, created_ts, "
                "text, source, region_name, reposts_count, comments_count, attitudes_count, "
                "is_top, is_long_text, pic_urls, retweeted_id, retweeted_text, raw_json, fetched_at) VALUES "
                "(:id,:uid,:bid,:user_name,:created_at,:created_ts,:text,:source,:region_name,"
                ":reposts_count,:comments_count,:attitudes_count,:is_top,:is_long_text,:pic_urls,"
                ":retweeted_id,:retweeted_text,:raw_json,:fetched_at) "
                "ON CONFLICT(id) DO UPDATE SET uid=excluded.uid, bid=excluded.bid, "
                "user_name=excluded.user_name, created_at=excluded.created_at, "
                "created_ts=excluded.created_ts, text=excluded.text, source=excluded.source, "
                "region_name=excluded.region_name, reposts_count=excluded.reposts_count, "
                "comments_count=excluded.comments_count, attitudes_count=excluded.attitudes_count, "
                "is_top=excluded.is_top, is_long_text=excluded.is_long_text, pic_urls=excluded.pic_urls, "
                "retweeted_id=excluded.retweeted_id, retweeted_text=excluded.retweeted_text, "
                "raw_json=excluded.raw_json, fetched_at=excluded.fetched_at",
                rows,
            )

    def recent_post_ids(self, uid: str, cutoff_ts: float) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM posts WHERE uid = ? AND created_ts >= ? AND comments_count > 0",
            (uid, cutoff_ts),
        ).fetchall()
        return [r["id"] for r in rows]

    def posts_pending_comments(self, uid: str) -> list[str]:
        """全量评论阶段的目标:评论未抓(comments_done=0)且有评论(comments_count>0)的博文。
        断点续抓靠它:中断后重跑只补没抓过的。"""
        rows = self.conn.execute(
            "SELECT id FROM posts WHERE uid = ? AND comments_done = 0 AND comments_count > 0 "
            "ORDER BY created_ts DESC",
            (uid,),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_comments_done(self, post_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE posts SET comments_done = 1 WHERE id = ?", (post_id,))

    # ---- 评论 ----
    def upsert_comments(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO comments (id, post_id, parent_id, created_at, text, "
                "like_counts, user_name, user_id, source, fetched_at) VALUES "
                "(:id,:post_id,:parent_id,:created_at,:text,:like_counts,:user_name,"
                ":user_id,:source,:fetched_at)",
                rows,
            )

    # ---- 统计(给 CLI 打印用) ----
    def counts(self) -> tuple[int, int]:
        p = self.conn.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"]
        c = self.conn.execute("SELECT COUNT(*) n FROM comments").fetchone()["n"]
        return p, c
