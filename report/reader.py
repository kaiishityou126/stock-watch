"""简报数据读取:从 data/app.db 读当天新文本(微博/雪球)+ 当天行情快照 + 龙虎榜。

按 lookback_hours 用 created_ts 圈"当天新增文本";行情/龙虎榜按 trade_date 取。
只读,不写。返回结构化 dict 供 prompt 组装。
"""

from __future__ import annotations

import sqlite3
import time


def _rows(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    # 某数据源从未运行时对应表不存在(如只用微博没跑过雪球)→ 当空结果,不崩简报
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise


def collect(db_path: str, lookback_hours: int, trade_date: str) -> dict:
    """汇总当天数据。trade_date 用于行情表(YYYYMMDD);文本按 now-lookback 圈选。"""
    cutoff = time.time() - lookback_hours * 3600
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        weibo = _rows(
            conn,
            "SELECT user_name, text, created_ts FROM posts WHERE created_ts >= ? "
            "AND text != '' ORDER BY created_ts DESC",
            (cutoff,),
        )
        xq = _rows(
            conn,
            "SELECT user_name, text, created_ts FROM xq_posts WHERE created_ts >= ? "
            "AND text != '' ORDER BY created_ts DESC",
            (cutoff,),
        )
        sectors = _rows(
            conn,
            "SELECT sector, sector_type, pct_change, turnover, volume, super_large_net, large_net, "
            "medium_net, small_net, main_net_inflow_est, retail_net_inflow_est "
            "FROM market_snapshot WHERE trade_date = ? ORDER BY pct_change DESC",
            (trade_date,),
        )
        indexes = _rows(
            conn,
            "SELECT index_code, index_name, close, pct_change, turnover, volume "
            "FROM index_snapshot WHERE trade_date = ? ORDER BY index_code",
            (trade_date,),
        )
        sentiment_rows = _rows(
            conn,
            "SELECT up_count, down_count, limit_up_count, limit_down_count, up_down_ratio, "
            "total_turnover, margin_balance FROM market_sentiment WHERE trade_date = ?",
            (trade_date,),
        )
        sentiment = sentiment_rows[0] if sentiment_rows else None
        dragon = _rows(
            conn,
            "SELECT stock_name, stock_code, reason, net_buy, inst_seat_count "
            "FROM dragon_tiger WHERE trade_date = ? ORDER BY net_buy DESC",
            (trade_date,),
        )
        # 热股榜:取库里 date<=trade_date 的倒数两个有数据日期(今日 + 前一有数据日),供算环比
        hot_dates = _rows(
            conn,
            "SELECT DISTINCT date FROM xq_hot_rank WHERE date <= ? ORDER BY date DESC LIMIT 2",
            (trade_date,),
        )
        dates = [r["date"] for r in hot_dates]
        hot_sql = (
            "SELECT symbol, name, rank, hot_value, market_type FROM xq_hot_rank "
            "WHERE date = ? ORDER BY rank"
        )
        hot_today = _rows(conn, hot_sql, (dates[0],)) if len(dates) >= 1 else []
        hot_prev = _rows(conn, hot_sql, (dates[1],)) if len(dates) >= 2 else []
        livenews = _rows(
            conn,
            "SELECT id, created_ts, created_at, text FROM xq_livenews WHERE created_ts >= ? "
            "ORDER BY created_ts DESC",
            (cutoff,),
        )
    finally:
        conn.close()

    return {
        "date": trade_date,
        "weibo_posts": weibo,
        "xq_posts": xq,
        "indexes": indexes,
        "sentiment": sentiment,
        "sectors": sectors,
        "dragon_tiger": dragon,
        "xq_hot_rank_today": hot_today,
        "xq_hot_rank_prev": hot_prev,
        "xq_livenews": livenews,
    }
