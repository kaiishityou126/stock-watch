"""行情快照 CLI:盘后拉当天大盘指数 + 市场情绪 + 板块资金流(行业+概念全量)+ 龙虎榜,
存进 index_snapshot / market_sentiment / market_snapshot / dragon_tiger。

  python -m market snapshot [--date YYYYMMDD] [--config config.json]

四段各自 try/except,任一失败记 ERROR 日志(模块名 market)不阻断其余,不写脏数据。
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from common import obs
from weibo.cli import load_config
from market.fetcher import (
    fetch_all_sectors,
    fetch_dragon_tiger,
    fetch_index,
    fetch_sentiment,
)
from market.storage import MarketStorage


def _total_turnover(indexes: list[dict]) -> float | None:
    """全市场成交额近似 = 上证综指(沪) + 深证成指(深)成交额(创业板指是深子集,不重复计)。"""
    total = 0.0
    got = False
    for r in indexes:
        if r.get("index_code") in ("sh000001", "sz399001") and r.get("turnover") is not None:
            try:
                total += float(r["turnover"])
                got = True
            except (TypeError, ValueError):
                pass
    return total if got else None


def cmd_snapshot(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("market", cfg["log_dir"])
    trade_date = args.date or date.today().strftime("%Y%m%d")
    store = MarketStorage(cfg["db_path"])
    failed = False
    indexes: list[dict] = []

    # ① 大盘指数
    try:
        indexes = fetch_index(trade_date)
        store.upsert_index(indexes)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 大盘指数 {len(indexes)} 条已入库")
        print(f"大盘指数：{len(indexes)} 条")
    except Exception as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 大盘指数失败：{e}")
        print(f"[error] 大盘指数失败：{e}")
        failed = True

    # ② 市场情绪(硬指标腿);全市场成交额用大盘指数汇总补全
    try:
        sentiment = fetch_sentiment(trade_date)
        if sentiment.get("total_turnover") is None:
            sentiment["total_turnover"] = _total_turnover(indexes)
        store.upsert_sentiment(sentiment)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 市场情绪已入库")
        print("市场情绪：OK")
    except Exception as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 市场情绪失败：{e}")
        print(f"[error] 市场情绪失败：{e}")
        failed = True

    # ③ 板块资金流(行业 + 概念全量)
    try:
        sectors = fetch_all_sectors(trade_date)
        store.upsert_snapshot(sectors)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 板块快照 {len(sectors)} 条已入库")
        print(f"板块快照：{len(sectors)} 条")
    except Exception as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 板块快照失败：{e}")
        print(f"[error] 板块快照失败：{e}")
        failed = True

    # ④ 龙虎榜(热度验真)
    try:
        dt = fetch_dragon_tiger(trade_date)
        store.upsert_dragon_tiger(dt)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 龙虎榜 {len(dt)} 条已入库")
        print(f"龙虎榜：{len(dt)} 条")
    except Exception as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 龙虎榜失败：{e}")
        print(f"[error] 龙虎榜失败：{e}")
        failed = True

    store.close()
    if failed:
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m market", description="A 股大盘/情绪/板块资金流/龙虎榜快照(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("snapshot", help="拉当天快照入库")
    sp.add_argument("--date", default="", help="交易日 YYYYMMDD(默认今天)")
    sp.set_defaults(func=cmd_snapshot)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
