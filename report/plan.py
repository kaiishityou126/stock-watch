"""report/plan.py — 简报 / 单项查询的唯一决策命令(把判断从 LLM 搬进代码)。

    python -m report.plan --date today --kind brief|item [--groups a,b] [--config ...] [--db ...]

输出单个 JSON action,调用方(skill)照做、不再自己判断:

  kind=brief:
    {"action": "send", "path": "<pdf 绝对路径>"}                         已有收盘完整版 PDF,直接发
    {"action": "generate", "date": "YYYYMMDD", "intraday": bool, "data": {...}}  用 data 渲染 PDF 再发
    {"action": "message", "text": "..."}                                只发这句话

  kind=item:
    {"action": "answer", "date": "YYYYMMDD", "intraday": bool, "data": {...}}    用 data 回文本
    {"action": "message", "text": "..."}

决策链(今天、需要行情腿时):
  有可复用 PDF(mtime>=阈值)→ send;非交易日 → "今日休市!";未开盘(<9:30)→ "尚未开盘!";
  否则补拉行情 → 读 → 有 A 股三大指数则 generate/answer(盘中数据标 intraday),
  补拉后仍无 → "行情暂时拉取失败,请稍后再试"。
历史日期:永不补拉;有 PDF→send,有数据→generate/answer,无→"该日无数据(最近有数据日 X)"。
纯雪球/龙虎榜的单项(不含行情腿)不判时段、不补拉,直接读答。

阈值:复用/决策统一用 config 的 report_reuse_after(默认 18:10)。intraday 标注沿用 reader 的
is_close_data(以 15:35 判数据是否盘中拉取),仅用于"盘中快照"提示,不参与决策。
stdout 只出纯 JSON(同 reader / render),出错也走 {"action":"message",...},exit 恒 0。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from common.stdio import force_utf8_stdio
from report import find
from report.reader import (
    ALL_GROUPS,
    DEFAULT_SCENARIO,
    SCENARIOS,
    VALID_GROUPS,
    _config_db_path,
    _has_any_data,
    _latest_trade_date,
    read_json,
    resolve_date,
)

_BJ_TZ = dt.timezone(dt.timedelta(hours=8))
_OPEN = (9, 30)  # A 股开盘(北京时)
_MARKET_GROUPS = ("index", "sentiment", "sectors")
_A_SHARE_INDEX = {"sh000001", "sz399001", "sz399006"}  # 上证/深成/创业板
_DEFAULT_DB = "data/app.db"
_DEFAULT_REUSE_AFTER = "18:10"
_TRADE_CAL_TTL = 7 * 86400   # 交易日历文件缓存有效期(秒);日历少变,一周足够
_BACKFILL_TIMEOUT = 120      # 补拉 market snapshot 最长等待(秒);超时按拉取失败兜底

MSG_CLOSED = "今日休市!"
MSG_PREOPEN = "尚未开盘!"
MSG_FETCH_FAIL = "行情暂时拉取失败,请稍后再试"


def _now_bj() -> dt.datetime:
    return dt.datetime.now(_BJ_TZ)


def is_trading_day(d: dt.date, cache_file: Optional[str] = None) -> bool:
    """d 是否 A 股交易日。带 TTL 文件缓存的 akshare 交易日历;akshare 挂时依次降级:
    过期缓存 → 非周末启发式(后者测不出法定节假日,只是兜底)。"""
    days = _read_trade_cache(cache_file, _TRADE_CAL_TTL) if cache_file else None
    if days is None:
        try:
            days = _fetch_trade_days()
            if cache_file:
                try:
                    Path(cache_file).write_text(json.dumps(sorted(days)), encoding="utf-8")
                except OSError:
                    pass
        except Exception:
            days = _read_trade_cache(cache_file, None) if cache_file else None  # 过期缓存也认
            if days is None:
                return d.weekday() < 5
    return d.isoformat() in days


def _trade_cache_file(db_path: str) -> str:
    return str(Path(db_path).parent / ".trade_cal.json")


def _read_trade_cache(cache_file: str, max_age: Optional[float]) -> Optional[set]:
    """读交易日历缓存;max_age=None 时忽略过期(供 akshare 挂时兜底)。"""
    try:
        p = Path(cache_file)
        if max_age is not None and time.time() - p.stat().st_mtime > max_age:
            return None
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _fetch_trade_days() -> set:
    import akshare as ak  # 延迟导入:无 akshare / 无网时由调用方降级

    cal = ak.tool_trade_date_hist_sina()
    return {str(x) for x in cal["trade_date"].tolist()}


def _backfill(trade_date: str, groups: Sequence[str], config_path: str) -> None:
    """subprocess 跑 market snapshot 补拉今天行情;失败不抛,由后续"读不到行情"分支兜。"""
    g = [x for x in groups if x in _MARKET_GROUPS] or list(_MARKET_GROUPS)
    try:
        subprocess.run(
            [sys.executable, "-m", "market", "snapshot",
             "--groups", ",".join(g), "--date", trade_date, "--config", config_path],
            capture_output=True, timeout=_BACKFILL_TIMEOUT,
        )
    except Exception:
        pass


def _has_market(data: dict) -> bool:
    """补拉后三条行情腿(大盘/情绪/板块)任一有数据即算拉到。

    只查 indexes 会漏判只请求 sentiment/sectors 的单项(那时 data 里没有 indexes 键)。
    """
    idx = data.get("indexes")
    if isinstance(idx, list) and any(r.get("index_code") in _A_SHARE_INDEX for r in idx):
        return True
    if data.get("sentiment"):  # collect 有行才回非空 dict
        return True
    sec = data.get("sectors")
    return isinstance(sec, dict) and any(sec.values())


def _latest_from_db(db_path: str, upper: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        return _latest_trade_date(conn, upper=upper)
    finally:
        conn.close()


def _make(kind: str, date_str: str, data: dict, intraday: bool = False) -> dict:
    action = "generate" if kind == "brief" else "answer"
    return {"action": action, "date": date_str, "intraday": intraday, "data": data}


def plan(
    date_symbol: str,
    kind: str,
    groups: Sequence[str],
    size: dict,
    db_path: str,
    config_path: str,
    reuse_after: str,
    now: Optional[dt.datetime] = None,
) -> dict:
    """决策核心。date_symbol 非法由调用方(main)捕获 resolve_date 的 ValueError。"""
    resolved = resolve_date(date_symbol)
    today = dt.date.today()
    resolved_str = resolved.strftime("%Y%m%d")
    if resolved > today:
        return {"action": "message", "text": f"{resolved_str} 是未来日期,还没有数据"}

    is_today = resolved == today
    is_brief = kind == "brief"
    needs_market = is_brief or bool(set(groups) & set(_MARKET_GROUPS))

    def read(dstr: str) -> dict:
        return read_json(db_path, groups, dstr, size, auto_date=False)

    # ── 历史日期:永不补拉 ──
    if not is_today:
        if is_brief:
            f = find.find_report(resolved_str, reuse_after=reuse_after)
            if f["exists"] and f["reusable"]:
                return {"action": "send", "path": f["path"]}
        data = read(resolved_str)
        if _has_any_data(data):
            return _make(kind, resolved_str, data)
        latest = _latest_from_db(db_path, resolved_str)
        return {"action": "message", "text": f"{resolved_str} 无数据(最近有数据日 {latest or '无'})"}

    # ── 今天 ──
    if is_brief:
        f = find.find_report(resolved_str, reuse_after=reuse_after)
        if f["exists"] and f["reusable"]:
            return {"action": "send", "path": f["path"]}

    # 纯雪球 / 龙虎榜的单项(不含行情腿)→ 不判时段、不补拉,直接读答
    if not needs_market:
        data = read(resolved_str)
        if _has_any_data(data):
            return _make(kind, resolved_str, data)
        return {"action": "message", "text": "今日暂无数据"}

    now = now or _now_bj()
    if not is_trading_day(today, _trade_cache_file(db_path)):
        return {"action": "message", "text": MSG_CLOSED}
    if (now.hour, now.minute) < _OPEN:
        return {"action": "message", "text": MSG_PREOPEN}

    _backfill(resolved_str, groups, config_path)
    data = read(resolved_str)
    if not _has_market(data):
        return {"action": "message", "text": MSG_FETCH_FAIL}
    intraday = not data.get("is_close_data", True)
    return _make(kind, resolved_str, data, intraday)


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(
        prog="python -m report.plan",
        description="简报/单项查询决策命令:出一个 action,调用方照做(纯 JSON)",
    )
    ap.add_argument("--date", default="today", help="today/yesterday/N-days-ago/last-tue/this-mon/YYYYMMDD")
    ap.add_argument("--kind", choices=["brief", "item"], default="brief", help="brief=完整简报(出PDF);item=单项查询(回文本)")
    ap.add_argument("--groups", default=None, help="逗号分隔:" + ",".join(sorted(VALID_GROUPS)) + ";缺省全部")
    ap.add_argument("--scenario", default=DEFAULT_SCENARIO, choices=sorted(SCENARIOS), help="size 预设档位")
    ap.add_argument("--db", default="", help="SQLite 库路径;缺省读 config.json 的 db_path")
    ap.add_argument("--config", default="config.json", help="配置文件路径(取 db_path / report_reuse_after)")
    args = ap.parse_args(argv)

    def msg(text: str) -> int:
        print(json.dumps({"action": "message", "text": text}, ensure_ascii=False))
        return 0

    db_path = args.db or _config_db_path(args.config) or _DEFAULT_DB
    groups = args.groups.split(",") if args.groups else list(ALL_GROUPS)
    unknown = set(groups) - VALID_GROUPS
    if unknown:
        return msg(f"未知数据块: {sorted(unknown)};可用: {sorted(VALID_GROUPS)}")
    size = dict(SCENARIOS[args.scenario])
    reuse_after = find._config_value(args.config, "report_reuse_after") or _DEFAULT_REUSE_AFTER

    try:
        out = plan(args.date, args.kind, groups, size, db_path, args.config, reuse_after)
    except ValueError as e:  # resolve_date 日期符号非法
        return msg(str(e))

    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
