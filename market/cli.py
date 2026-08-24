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
from common.stdio import force_utf8_stdio
from weibo.cli import load_config
from market.fetcher import (
    fetch_all_sectors,
    fetch_dragon_tiger,
    fetch_index,
    fetch_sentiment,
)
from market.storage import MarketStorage

# snapshot 可单独拉的块。缺省 = 全量拉(定时 cron 行为不变)。
# sentiment 的 total_turnover 依赖 index;单拉 sentiment 时会顺带取 index,但只落 sentiment 行。
SNAPSHOT_GROUPS = ("index", "sentiment", "sectors", "dragon")


def _total_turnover(indexes: list[dict]) -> float | None:
    """全市场成交额近似 = 上证综指(沪) + 深证成指(深)成交额(创业板指是深子集,不重复计)。

    两腿必须齐才出数:缺一腿的"半边和"会被 upsert 的 COALESCE 固化成当日常驻值,
    宁可 None 留给下次补齐。
    """
    vals: dict[str, float] = {}
    for r in indexes:
        code = r.get("index_code")
        if code in ("sh000001", "sz399001") and r.get("turnover") is not None:
            try:
                vals[code] = float(r["turnover"])
            except (TypeError, ValueError):
                pass
    if "sh000001" not in vals or "sz399001" not in vals:
        return None
    return vals["sh000001"] + vals["sz399001"]


def cmd_snapshot(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("market", cfg["log_dir"])
    trade_date = args.date or date.today().strftime("%Y%m%d")
    store = MarketStorage(cfg["db_path"])
    groups = args.groups.split(",") if args.groups else None
    all_groups = groups is None
    want = set(groups) if groups is not None else set()
    unknown = want - set(SNAPSHOT_GROUPS)
    if unknown:
        raise SystemExit(f"未知数据块: {sorted(unknown)};可用: {','.join(SNAPSHOT_GROUPS)}")
    failed = False
    indexes: list[dict] = []

    def has(name: str) -> bool:
        return all_groups or name in want

    # ① 大盘指数(盘中按需拉也只拉这一块时,不给板块/情绪)
    if has("index"):
        try:
            indexes = fetch_index(trade_date)
            store.upsert_index(indexes)
            obs.log(logger, logging.INFO, "行情", f"{trade_date} 大盘指数 {len(indexes)} 条已入库")
            print(f"大盘指数：{len(indexes)} 条")
        except Exception as e:
            obs.log(logger, logging.ERROR, "行情", f"{trade_date} 大盘指数失败：{e}")
            print(f"[error] 大盘指数失败：{e}")
            failed = True
    # sentiment 的 total_turnover 需要大盘指数汇总;单拉 sentiment 时顺带取 index,但不落库
    if has("sentiment") and not has("index"):
        try:
            indexes = fetch_index(trade_date)
        except Exception:
            indexes = []

    # ② 市场情绪(硬指标腿);全市场成交额用大盘指数汇总补全
    if has("sentiment"):
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
    if has("sectors"):
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
    if has("dragon"):
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
    # skill 的按需补拉经 Hermes 终端工具读输出,强制 UTF-8 防控制台 GBK 乱码
    force_utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="python -m market", description="A 股大盘/情绪/板块资金流/龙虎榜快照(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("snapshot", help="拉当天快照入库")
    sp.add_argument("--date", default="", help="交易日 YYYYMMDD(默认今天)")
    sp.add_argument("--groups", default="", help="逗号分隔要拉的块:" + ",".join(SNAPSHOT_GROUPS) + "(缺省全量)")
    sp.set_defaults(func=cmd_snapshot)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
