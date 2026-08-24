"""行情快照存储层(与微博/雪球共用 data/app.db)。

四张表(自顶向下发现漏斗:大盘 → 情绪 → 板块 → 龙虎榜验真):
  index_snapshot   按 (trade_date, index_code) 存大盘指数。
      A 股三大指数存 close/pct_change/turnover/volume;海外指数 turnover/volume 为 NULL。
  market_sentiment 按 trade_date 存市场情绪(一天一行)。
      涨跌家数/涨跌停/成交额/两融余额,硬指标腿(与舆情文本腿并列喂 LLM)。
  market_snapshot  按 (trade_date, sector, sector_type) 存板块级行情。
      sector_type 区分 行业/概念,两类同表。
      直接口径:pct_change / turnover(成交额) / volume(成交量)。
      四级净额(估算、非真实机构/散户):super_large_net / large_net / medium_net / small_net。
      合成口径(字段名带 _est):
        main_net_inflow_est   主力净流入(超大+大单)≈机构
        retail_net_inflow_est 中小单净流入 ≈ 散户
  dragon_tiger 龙虎榜,按 (trade_date, stock_code, reason) 去重;
      inst_seat_count = 机构专用席位数(热度验真,与资金流分开)。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_snapshot (
    trade_date  TEXT NOT NULL,
    index_code  TEXT NOT NULL,
    index_name  TEXT,
    close       REAL,
    pct_change  REAL,
    turnover    REAL,   -- A股三大指数有;海外为 NULL
    volume      REAL,   -- A股三大指数有;海外为 NULL
    source      TEXT,
    fetched_at  TEXT,
    PRIMARY KEY (trade_date, index_code)
);

CREATE TABLE IF NOT EXISTS market_sentiment (
    trade_date        TEXT PRIMARY KEY,
    up_count          INTEGER,
    down_count        INTEGER,
    limit_up_count    INTEGER,
    limit_down_count  INTEGER,
    up_down_ratio     REAL,
    total_turnover    REAL,
    margin_balance    REAL,   -- 两融余额(T+1 慢数据),取不到为 NULL
    fetched_at        TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshot (
    trade_date            TEXT NOT NULL,
    sector                TEXT NOT NULL,
    sector_type           TEXT NOT NULL DEFAULT '行业',  -- 行业 / 概念
    pct_change            REAL,
    turnover              REAL,   -- 成交额(直接口径),缺数据时为 NULL
    volume                REAL,   -- 成交量(直接口径),缺数据时为 NULL
    super_large_net       REAL,   -- 超大单净额
    large_net             REAL,   -- 大单净额
    medium_net            REAL,   -- 中单净额
    small_net             REAL,   -- 小单净额
    main_net_inflow_est   REAL,   -- 主力净流入估算(超大+大单),非真实机构
    retail_net_inflow_est REAL,   -- 中小单净流入估算,非真实散户
    source                TEXT,
    fetched_at            TEXT,
    PRIMARY KEY (trade_date, sector, sector_type)
);

CREATE TABLE IF NOT EXISTS dragon_tiger (
    trade_date      TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    stock_name      TEXT,
    reason          TEXT NOT NULL,
    net_buy         REAL,
    buy_amount      REAL,
    sell_amount     REAL,
    inst_seat_count INTEGER,      -- 机构专用席位数(真机构信号)
    raw_json        TEXT,
    fetched_at      TEXT,
    PRIMARY KEY (trade_date, stock_code, reason)
);
"""


class MarketStorage:
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

    def upsert_index(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO index_snapshot (trade_date, index_code, index_name, close, "
                "pct_change, turnover, volume, source, fetched_at) VALUES "
                "(:trade_date,:index_code,:index_name,:close,:pct_change,:turnover,:volume,"
                ":source,:fetched_at) "
                "ON CONFLICT(trade_date, index_code) DO UPDATE SET index_name=excluded.index_name, "
                "close=excluded.close, pct_change=excluded.pct_change, turnover=excluded.turnover, "
                "volume=excluded.volume, source=excluded.source, fetched_at=excluded.fetched_at",
                rows,
            )

    def upsert_sentiment(self, row: dict) -> None:
        if not row:
            return
        with self._tx() as c:
            c.execute(
                "INSERT INTO market_sentiment (trade_date, up_count, down_count, limit_up_count, "
                "limit_down_count, up_down_ratio, total_turnover, margin_balance, fetched_at) VALUES "
                "(:trade_date,:up_count,:down_count,:limit_up_count,:limit_down_count,:up_down_ratio,"
                ":total_turnover,:margin_balance,:fetched_at) "
                "ON CONFLICT(trade_date) DO UPDATE SET up_count=excluded.up_count, "
                "down_count=excluded.down_count, limit_up_count=excluded.limit_up_count, "
                "limit_down_count=excluded.limit_down_count, up_down_ratio=excluded.up_down_ratio, "
                # 慢/易缺字段:新值为 NULL 时保留库中已有值,不让补抓冲掉
                "total_turnover=COALESCE(excluded.total_turnover, market_sentiment.total_turnover), "
                "margin_balance=COALESCE(excluded.margin_balance, market_sentiment.margin_balance), "
                "fetched_at=excluded.fetched_at",
                row,
            )

    def upsert_snapshot(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO market_snapshot (trade_date, sector, sector_type, pct_change, turnover, "
                "volume, super_large_net, large_net, medium_net, small_net, main_net_inflow_est, "
                "retail_net_inflow_est, source, fetched_at) VALUES "
                "(:trade_date,:sector,:sector_type,:pct_change,:turnover,:volume,:super_large_net,"
                ":large_net,:medium_net,:small_net,:main_net_inflow_est,:retail_net_inflow_est,"
                ":source,:fetched_at) "
                "ON CONFLICT(trade_date, sector, sector_type) DO UPDATE SET pct_change=excluded.pct_change, "
                "turnover=excluded.turnover, volume=excluded.volume, "
                "super_large_net=excluded.super_large_net, large_net=excluded.large_net, "
                "medium_net=excluded.medium_net, small_net=excluded.small_net, "
                "main_net_inflow_est=excluded.main_net_inflow_est, "
                "retail_net_inflow_est=excluded.retail_net_inflow_est, "
                "source=excluded.source, fetched_at=excluded.fetched_at",
                rows,
            )

    def upsert_dragon_tiger(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO dragon_tiger (trade_date, stock_code, stock_name, reason, "
                "net_buy, buy_amount, sell_amount, inst_seat_count, raw_json, fetched_at) VALUES "
                "(:trade_date,:stock_code,:stock_name,:reason,:net_buy,:buy_amount,:sell_amount,"
                ":inst_seat_count,:raw_json,:fetched_at)",
                rows,
            )

    def index_for(self, trade_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM index_snapshot WHERE trade_date = ? ORDER BY index_code",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def sentiment_for(self, trade_date: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM market_sentiment WHERE trade_date = ?", (trade_date,)
        ).fetchone()
        return dict(r) if r else None

    def snapshot_for(self, trade_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM market_snapshot WHERE trade_date = ? ORDER BY pct_change DESC",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def dragon_tiger_for(self, trade_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM dragon_tiger WHERE trade_date = ? ORDER BY net_buy DESC",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]
