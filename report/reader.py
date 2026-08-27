"""简报数据读取:从 data/app.db 读当天新文本(微博/雪球)+ 当天行情快照 + 龙虎榜。

按 lookback_hours 用 created_ts 圈"当天新增文本";行情/龙虎榜按 trade_date 取。
只读,不写。返回结构化 dict 供 prompt 组装。

CLI(agent 用 terminal 执行,stdout 只出纯 JSON):
    python -m report.reader [--date 符号] [--groups a,b,c] [--size g:N,...]
        [--scenario light|heavy] [--lookback H] [--db PATH] [--config PATH]
    --date 可取值:today / yesterday / N-days-ago / last-mon..last-sun /
                   this-mon..this-sun / YYYYMMDD;缺省自动取库中最近交易日
    --groups:index,sentiment,sectors,hot_rank,livenews,dragon;缺省全部。
        另有可选块 weibo_posts(博主博文,blogger-strategy 用):仅显式指定时读取,
        uid 取 config.json 的 bloggers 清单,每人最新 N 条(--size weibo_posts:N,light 档 200),
        无时间窗(长期画像要看足历史,不吃 lookback)
    --size:每组条数上限,如 sectors:20,hot_rank:20;覆盖 --scenario 预设
    --scenario:light(默认)/ heavy 两档 size 预设
    --lookback:快讯时间窗小时数,默认 24
    --db:SQLite 库路径;缺省读 --config 指向的 config.json 的 db_path,再缺省 data/app.db
    --config:配置文件路径(取 db_path),默认 config.json
    精确指定 --date 时按当天读,当天无数据则返回 {"error": "..."},不静默换日期
    不传 --date(自动取最近交易日)时,若库里没有今天数据会回退到旧日期,此时
    输出 is_today_data:false,让下游决定补拉而非拿旧日期数据冒充当天
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from common.stdio import force_utf8_stdio

# 库路径缺省链:--db 未指定 → 读 config.json 的 db_path → 再缺省回退 data/app.db。
# reader 只读,不校验 config 的博主列表(那是抓取侧的职责)。
_DEFAULT_DB = "data/app.db"

# 板块切组默认条数:light = 轻量日报,heavy = 深度复盘(用 --scenario 或 --size 覆盖)
SCENARIOS = {
    "light": {"sectors": 20, "hot_rank": 20, "livenews": 15, "dragon": 10, "weibo_posts": 200},
    "heavy": {"sectors": 30, "hot_rank": 50, "livenews": 30, "dragon": 20, "weibo_posts": 300},
}
DEFAULT_SCENARIO = "light"

# 完整简报用到的数据块(weibo/xq 博主全文不进日报,留作 collect 旧行为内部使用)。
# weibo_posts 是可选块:uid 由 config.json 驱动、每人最新 post_limit 条,只服务策略提炼,
# 不进"缺省全部"(否则定时日报会把几万字博文灌进 prompt),仅显式 --groups 指定时读取。
ALL_GROUPS = ["index", "sentiment", "sectors", "hot_rank", "livenews", "dragon"]
POST_GROUP = "weibo_posts"
VALID_GROUPS = set(ALL_GROUPS) | {POST_GROUP}

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# 收盘数据判据:行情腿 fetched_at(北京时) >= 15:35 才算收盘数据
# (15:00 收盘后留缓冲;cron 现定 18:00 拉,天然落在门槛后)。
# fetched_at 各腿写库时是 UTC ISO 字符串。
_BJ_TZ = dt.timezone(dt.timedelta(hours=8))
CLOSE_HOUR, CLOSE_MINUTE = 15, 35


def _fetched_at_bj(fetched_at: str) -> dt.datetime | None:
    """fetched_at(UTC ISO 字符串) → 北京时 datetime;解析失败返回 None。"""
    try:
        d = dt.datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(_BJ_TZ)


def _close_and_time(data: dict) -> tuple[bool, str | None]:
    """(是否收盘数据, 今天行情腿最新拉取时间北京时 HH:MM)。

    历史日期恒 (True, None)——那天已过,库里只有完整收盘值;
    今天则看行情腿(大盘/情绪/板块)fetched_at 北京时是否 >= 15:35:
      - 请求了行情腿但全空(交易日盘中还没拉过)→ (False, None),触发按需补拉;
      - 有数据但拉取时刻 < 15:35 → (False, 拉取时刻),盘中快照,标注时间;
      - 有数据且拉取时刻 >= 15:35 → (True, 拉取时刻),可当盘后复盘。
    """
    if data.get("date") != _today_str():
        return True, None
    market_present = any(k in data for k in ("indexes", "sectors", "sentiment"))
    latest: str | None = None
    for key in ("indexes", "sectors", "sentiment"):
        v = data.get(key)
        rows = [v] if isinstance(v, dict) else (v if isinstance(v, list) else [])
        for r in rows:
            fa = r.get("fetched_at") if isinstance(r, dict) else None
            if fa and (latest is None or fa > latest):
                latest = fa
    if not market_present:
        return True, None  # 没请求行情腿(如只问热榜/龙虎榜)→ 不判
    if not latest:
        return False, None  # 请求了行情腿但今天全空 → 未拉取,补拉
    bj = _fetched_at_bj(latest)
    if bj is None:
        return True, None
    is_close = (bj.hour, bj.minute) >= (CLOSE_HOUR, CLOSE_MINUTE)
    return is_close, bj.strftime("%H:%M")


def _rows(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    # 某数据源从未运行时对应表不存在(如只用微博没跑过雪球)→ 当空结果,不崩简报
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise


def _today_str() -> str:
    return dt.date.today().strftime("%Y%m%d")


def _latest_trade_date(conn: sqlite3.Connection, upper: Optional[str] = None) -> Optional[str]:
    """库中 <= upper 的最近交易日。index_snapshot 优先,表不存在/为空时依次回退。"""
    for table, col in (
        ("index_snapshot", "trade_date"),
        ("market_snapshot", "trade_date"),
        ("market_sentiment", "trade_date"),
        ("xq_hot_rank", "date"),
    ):
        try:
            sql = f"SELECT MAX({col}) FROM {table}"
            args: tuple = ()
            if upper is not None:
                sql += f" WHERE {col} <= ?"
                args = (upper,)
            row = conn.execute(sql, args).fetchone()
            if row and row[0]:
                return str(row[0])
        except sqlite3.OperationalError:
            continue
    return None


def resolve_date(symbol: str) -> dt.date:
    """把日期符号解析成具体日期。纯日历运算,不看库里有没有数据。"""
    today = dt.date.today()
    if symbol == "today":
        return today
    if symbol == "yesterday":
        return today - dt.timedelta(days=1)
    m = re.fullmatch(r"(\d+)-days-ago", symbol)
    if m:
        return today - dt.timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"last-(mon|tue|wed|thu|fri|sat|sun)", symbol)
    if m:
        wd = _WEEKDAYS[m.group(1)]
        return today - dt.timedelta(days=(today.weekday() - wd) % 7 + 7)
    m = re.fullmatch(r"this-(mon|tue|wed|thu|fri|sat|sun)", symbol)
    if m:
        # 本周真实日历日(未到就是未来日期)。不能往回取整——周三问 this-fri
        # 回退上周五会让 LLM 把上周数据当本周五答;未来日期无数据由 main 报错兜住
        wd = _WEEKDAYS[m.group(1)]
        monday = today - dt.timedelta(days=today.weekday())
        return monday + dt.timedelta(days=wd)
    if re.fullmatch(r"\d{8}", symbol):
        return dt.datetime.strptime(symbol, "%Y%m%d").date()
    raise ValueError(f"无法识别的日期符号: {symbol}。可用 today / yesterday / N-days-ago / last-tue / this-mon / YYYYMMDD")


def collect(
    db_path: str,
    lookback_hours: int = 24,
    trade_date: Optional[str] = None,
    groups: Optional[Sequence[str]] = None,
    post_uids: Optional[Sequence[str]] = None,
    post_limit: int = 200,
) -> dict:
    """汇总数据。trade_date 为 None 时自动取库中最近交易日(参考日)。

    groups 为 None 时读全部数据块(含微博/雪球博主全文,供 collect 旧调用方/测试);
    指定 groups 时只读列出的块,返回键与 groups 对应,并始终带 date。
    POST_GROUP 块需要调用方传入 post_uids(来自 config.json 的 bloggers),每人最新
    post_limit 条;post_uids 缺省/为空时该块返回空列表。
    """
    cutoff = time.time() - lookback_hours * 3600
    named_date = trade_date is not None  # 点名日期时快讯按该自然日过滤,不用最近窗口冒充
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(conn, upper=_today_str())
        result: dict = {"date": trade_date}
        all_groups = groups is None
        want = set(groups) if groups is not None else set()

        def has(name: str) -> bool:
            return all_groups or name in want

        if has("weibo"):
            result["weibo_posts"] = _rows(
                conn,
                "SELECT user_name, text, created_ts FROM posts WHERE created_ts >= ? "
                "AND text != '' ORDER BY created_ts DESC",
                (cutoff,),
            )
        if has("xq"):
            result["xq_posts"] = _rows(
                conn,
                "SELECT user_name, text, created_ts FROM xq_posts WHERE created_ts >= ? "
                "AND text != '' ORDER BY created_ts DESC",
                (cutoff,),
            )
        if has("sectors"):
            result["sectors"] = _rows(
                conn,
                "SELECT sector, sector_type, pct_change, turnover, volume, super_large_net, large_net, "
                "medium_net, small_net, main_net_inflow_est, retail_net_inflow_est, fetched_at "
                "FROM market_snapshot WHERE trade_date = ? ORDER BY pct_change DESC",
                (trade_date,),
            )
        if has("index"):
            result["indexes"] = _rows(
                conn,
                "SELECT index_code, index_name, close, pct_change, turnover, volume, fetched_at "
                "FROM index_snapshot WHERE trade_date = ? ORDER BY index_code",
                (trade_date,),
            )
        if has("sentiment"):
            sentiment_rows = _rows(
                conn,
                "SELECT up_count, down_count, limit_up_count, limit_down_count, up_down_ratio, "
                "total_turnover, margin_balance, fetched_at FROM market_sentiment WHERE trade_date = ?",
                (trade_date,),
            )
            result["sentiment"] = sentiment_rows[0] if sentiment_rows else None
        if has("dragon"):
            result["dragon_tiger"] = _rows(
                conn,
                "SELECT stock_name, stock_code, reason, net_buy, inst_seat_count "
                "FROM dragon_tiger WHERE trade_date = ? ORDER BY net_buy DESC",
                (trade_date,),
            )
        if has("hot_rank"):
            # 热股榜:取库里 date<=trade_date 的倒数两个有数据日期(今日 + 前一有数据日),供算环比;
            # 行内带 date(实际归档日),点名日期无归档回退到更早日期时下游可如实标注
            hot_dates = _rows(
                conn,
                "SELECT DISTINCT date FROM xq_hot_rank WHERE date <= ? ORDER BY date DESC LIMIT 2",
                (trade_date,),
            )
            dates = [r["date"] for r in hot_dates]
            hot_sql = (
                "SELECT symbol, name, rank, hot_value, market_type, date FROM xq_hot_rank "
                "WHERE date = ? ORDER BY rank"
            )
            result["xq_hot_rank_today"] = _rows(conn, hot_sql, (dates[0],)) if len(dates) >= 1 else []
            result["xq_hot_rank_prev"] = _rows(conn, hot_sql, (dates[1],)) if len(dates) >= 2 else []
        if has("livenews"):
            if named_date and trade_date:
                # 点名历史日期:只出该自然日(北京时)归档的快讯;没有就是没有,
                # 不拿"最近 24h"的内容贴那天的标签(否则 LLM 会把今天新闻说成历史日期的)
                day0 = dt.datetime.strptime(trade_date, "%Y%m%d").replace(
                    tzinfo=dt.timezone(dt.timedelta(hours=8)))
                start = int(day0.timestamp())
                result["xq_livenews"] = _rows(
                    conn,
                    "SELECT id, created_ts, created_at, text FROM xq_livenews "
                    "WHERE created_ts >= ? AND created_ts < ? ORDER BY created_ts DESC",
                    (start, start + 86400),
                )
            else:
                result["xq_livenews"] = _rows(
                    conn,
                    "SELECT id, created_ts, created_at, text FROM xq_livenews WHERE created_ts >= ? "
                    "ORDER BY created_ts DESC",
                    (cutoff,),
                )
        if not all_groups and POST_GROUP in want:
            # 博主博文块(策略提炼):per-uid 单独查询,uids 个位数、开销可忽略,
            # 换来天然的每人 top-N。created_ts 顺转北京时可读串,LLM 标注时间跨度免算 epoch。
            # 注意不能用 has():旧内部块 weibo 的输出键同为 weibo_posts,全块模式(groups=None)
            # 下若在这里重查会用空结果把它覆盖掉
            posts: list[dict] = []
            for uid in post_uids or ():
                posts.extend(_rows(
                    conn,
                    "SELECT uid, user_name, text, reposts_count, comments_count, attitudes_count, "
                    "datetime(created_ts, 'unixepoch', '+8 hours') AS created_bj "
                    "FROM posts WHERE uid = ? AND text != '' ORDER BY created_ts DESC LIMIT ?",
                    (str(uid), int(post_limit)),
                ))
            result[POST_GROUP] = posts
    finally:
        conn.close()

    # 收盘数据判据:今天数据是 15:35 后拉的才算收盘;盘中拉的标注快照时刻
    is_close, time_bj = _close_and_time(result)
    result["is_close_data"] = is_close
    if time_bj is not None:
        result["fetched_at_bj"] = time_bj
    return result


# collect 输出里的元数据键,不算"数据"(否则 is_close_data:true 会让空库被误判为有数据)
_META_KEYS = {"date", "is_close_data", "fetched_at_bj", "is_today_data"}


def _has_any_data(data: dict) -> bool:
    """data 里除元数据键外是否还有任何非空数据。sectors 已是切组后的 dict,要递归看子表。"""
    for k, v in data.items():
        if k in _META_KEYS:
            continue
        if isinstance(v, dict):
            if any(v.values()):
                return True
        elif v:
            return True
    return False


def _split_sectors(sectors: list[dict], n: int) -> dict[str, list[dict]]:
    """板块切成 4 组:领涨/领跌/主力净流入/净流出,各取前 n。"""
    if not sectors:
        return {"gainers": [], "losers": [], "inflow": [], "outflow": []}
    gainers = sorted(sectors, key=lambda r: r["pct_change"] or 0, reverse=True)[:n]
    losers = sorted(sectors, key=lambda r: r["pct_change"] or 0)[:n]
    inflow = sorted(sectors, key=lambda r: r["main_net_inflow_est"] or 0, reverse=True)[:n]
    outflow = sorted(sectors, key=lambda r: r["main_net_inflow_est"] or 0)[:n]
    return {"gainers": gainers, "losers": losers, "inflow": inflow, "outflow": outflow}


def _config_db_path(config_path: str) -> str | None:
    """从 config.json 读 db_path;配置缺失/无该键返回 None(调用方回退默认)。

    reader 只读,不做 load_config 那套博主校验——这里仅取库路径,失败静默回退。
    """
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = data.get("db_path")
    return str(v) if v else None


def _config_bloggers(config_path: str) -> list[str]:
    """从 config.json 读 bloggers[].uid(去重保序);文件缺失/解析失败/为空返回 []。

    与抓取侧 load_config 不同:只读不校验、失败不抛,是否算配置错误由调用方
    (请求了 POST_GROUP 却拿不到 uid 时 fail)决定。
    """
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for b in data.get("bloggers") or []:
        uid = str(b.get("uid", "")).strip()
        if uid and uid not in out:
            out.append(uid)
    return out


def _stale_today(data: dict, groups: Sequence[str], auto_date: bool) -> bool:
    """自动取最近交易日的路径下,请求了行情腿但库里没有今天数据。

    定时/手动触发不传 --date 时,collect 会静默回退到最近有数据日(可能昨天),
    且 is_close_data 因 date!=今天被恒判 true,下游无法察觉"今天还没拉"。
    此处给显式信号:date 缺失或 < 今天 → 今天数据未入库,应先补拉再生成。
    用户点名历史日期(auto_date=False)或没请求行情腿时不触发。
    """
    if not auto_date:
        return False
    if not any(g in groups for g in ("index", "sentiment", "sectors")):
        return False
    d = data.get("date")
    return not d or d < _today_str()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="A股舆情简报数据读取(只读,输出纯 JSON)")
    parser.add_argument("--date", default=None, help="today/yesterday/N-days-ago/last-tue/this-mon/YYYYMMDD;缺省自动取最近交易日")
    parser.add_argument("--groups", default=None, help="逗号分隔:" + ",".join(VALID_GROUPS) + ";缺省全部")
    parser.add_argument("--size", default=None, help="每组条数上限,逗号分隔 组:N,如 sectors:20,hot_rank:20")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO, choices=sorted(SCENARIOS), help="size 预设档位")
    parser.add_argument("--lookback", type=int, default=24, help="快讯时间窗(小时),默认 24")
    parser.add_argument("--db", default="", help="SQLite 库路径;缺省读 config.json 的 db_path")
    parser.add_argument("--config", default="config.json", help="配置文件路径(取 db_path),默认 config.json")
    args = parser.parse_args(argv)
    db_path = args.db or _config_db_path(args.config) or _DEFAULT_DB

    # 强制 UTF-8:agent 的 terminal 工具按 UTF-8 读 stdout,不受控制台代码页影响
    force_utf8_stdio()

    def fail(msg: str) -> int:
        print(json.dumps({"error": msg}, ensure_ascii=False))
        return 0

    groups = args.groups.split(",") if args.groups else list(ALL_GROUPS)
    unknown = set(groups) - VALID_GROUPS
    if unknown:
        return fail(f"未知数据块: {sorted(unknown)};可用: {sorted(VALID_GROUPS)}")

    size = dict(SCENARIOS[args.scenario])
    if args.size:
        for item in args.size.split(","):
            if ":" not in item:
                return fail(f"--size 格式应为 组:条数,如 sectors:20;收到: {item!r}")
            k, v = item.split(":", 1)
            if k not in VALID_GROUPS:
                return fail(f"--size 指定了未知数据块: {k}")
            try:
                n = int(v)
            except ValueError:
                return fail(f"--size 条数应为整数,收到: {v!r}")
            if n <= 0:
                return fail(f"--size 条数应为正整数,收到: {v!r}")
            size[k] = n

    resolved_str: Optional[str] = None
    if args.date is not None:
        try:
            resolved_str = resolve_date(args.date).strftime("%Y%m%d")
        except ValueError as e:
            return fail(str(e))
        except OverflowError:
            # N-days-ago 数字过大超出日期范围:报错而非 traceback(stdout 必须保持纯 JSON)
            return fail(f"日期超出可表示范围: {args.date}")
        if resolved_str > _today_str():
            # 未来日期不可能有数据;不拦的话热榜回退/最近帖子会把它"填活",
            # 绕过下面的无数据守卫,把旧数据贴上未来日期的标签
            return fail(f"{resolved_str} 是未来日期,还没有数据")

    # 博主博文块:uid 从 config.json 读;请求了该块却拿不到任何 uid 属于配置错误
    post_uids: Optional[list[str]] = None
    if POST_GROUP in groups:
        post_uids = _config_bloggers(args.config)
        if not post_uids:
            return fail(f"{args.config} 未配置 bloggers(uid),无法读取博主博文")

    data = collect(
        db_path,
        lookback_hours=args.lookback,
        trade_date=resolved_str,
        groups=groups,
        post_uids=post_uids,
        post_limit=size.get(POST_GROUP, SCENARIOS[args.scenario][POST_GROUP]),
    )

    # 自动取日期且库里没有今天数据(请求了行情腿)→ 显式标记,让下游决定补拉
    # 而非拿回退到的旧日期数据冒充当天(点名历史日期不标记)
    if _stale_today(data, groups, args.date is None):
        data["is_today_data"] = False

    # 板块切成 4 组(领涨/领跌/主力净流入/净流出)——collect 只按 pct_change 排序取全量,切组在 CLI
    if "sectors" in groups:
        n = size.get("sectors", SCENARIOS[args.scenario]["sectors"])
        data["sectors"] = _split_sectors(data.get("sectors", []), n)

    # 其余列表块按 size 截断(collect 已按各自口径排序,截前 N 即可)
    for key, group in (("indexes", "index"), ("xq_hot_rank_today", "hot_rank"),
                       ("xq_hot_rank_prev", "hot_rank"), ("xq_livenews", "livenews"),
                       ("dragon_tiger", "dragon")):
        n = size.get(group)
        if n and isinstance(data.get(key), list):
            data[key] = data[key][:n]

    # 精确指定日期且无任何数据 → 报告无数据,不静默换日期
    if args.date is not None and not _has_any_data(data):
        conn = sqlite3.connect(db_path)
        try:
            latest = _latest_trade_date(conn, upper=resolved_str)
        finally:
            conn.close()
        return fail(f"{resolved_str} 无数据;最近有数据日 {latest or '无'}")

    print(json.dumps(data, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
