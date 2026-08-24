"""雪球 CLI 编排(镜像 weibo 包)。

命令:
  python -m xueqiu login                    登录(首次 / 失效时)
  python -m xueqiu crawl                     抓当天热股榜(快照 upsert)+ 7x24 快讯(增量)
                                             --mode 参数保留仅为 CLI 契约一致;对热榜无意义,
                                             增量语义仅作用于 7x24。

一期已从“按博主抓时间线+评论”改为“抓全站注意力信号”。旧 user_timeline 相关代码
(crawl_one / _uids)保留但不再接入每日流程(未来三期大V策略提炼可能复用)。

复用 weibo 包的 load_config(同 config.json)，读 xueqiu_bloggers / xq_* 配置项。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from common import obs
from . import login as login_mod
from common.errors import AuthError
from common.stdio import force_utf8_stdio
from weibo.cli import load_config
from .crawler import (
    XueqiuClient,
    now_iso,
    select_xq_comments,
    slim_hot_rank,
    slim_xq_post,
)
from .storage import XueqiuStorage


def _uids(cfg: dict) -> list[str]:
    bloggers = cfg.get("xueqiu_bloggers") or []
    uids = [str(b.get("user_id", "")).strip() for b in bloggers if b.get("user_id")]
    if not uids:
        raise SystemExit("config.json 的 xueqiu_bloggers 列表为空:至少填一个 user_id")
    return uids


def _make_client(cfg: dict, need_login: bool = False) -> XueqiuClient:
    """构造雪球 client。默认(need_login=False)走匿名:首次请求自动 GET /hq 拿匿名 token,
    公开数据(热榜/7x24/博主时间线)免扫码。仅当未来拓需登录数据源(自选股/关注流等)
    才传 need_login=True,那时才用 .xq_profile 登录 cookie。"""
    if need_login:
        cookie = login_mod.get_cookie_header(cfg["xq_profile_dir"])
        return XueqiuClient(
            cookie,
            cfg["min_delay"],
            cfg["max_delay"],
            refresh_cookie=lambda: login_mod.get_cookie_header(cfg["xq_profile_dir"]),
        )
    return XueqiuClient(None, cfg["min_delay"], cfg["max_delay"])


def crawl_one(client: XueqiuClient, store: XueqiuStorage, uid: str, mode: str, cfg: dict) -> tuple[int, int]:
    """抓一个雪球博主,返回 (新增博文数, 新增/更新评论条数)。"""
    scope = cfg["xq_comment_scope"]
    prev_ts = store.get_last_ts(uid) if mode == "incremental" else 0

    if mode == "full":
        start_page, full_done = store.get_full_progress(uid)
        if full_done:
            start_page = 0  # 上次已到底,重跑全量从头
        start_page = max(start_page, 1)
    else:
        start_page = 1

    new_posts: list[dict] = []
    seen: set[str] = set()
    reached_old = False
    long_fail: list[str] = []

    page = start_page
    max_page = start_page
    while page <= cfg["xq_max_pages"]:
        statuses, max_page = client.fetch_timeline(uid, page)
        if not statuses:
            break
        batch: list[dict] = []
        for s in statuses:
            pid = str(s.get("id", ""))
            if not pid or pid in seen:
                continue
            ts = None
            try:
                ts = int(int(s.get("created_at")) / 1000)
            except (TypeError, ValueError):
                ts = None
            if mode == "incremental" and prev_ts and ts is not None and ts <= prev_ts:
                reached_old = True
                continue
            seen.add(pid)
            full = None
            from .crawler import _needs_full  # 局部导入，避免污染模块级命名
            if cfg["enable_full_text"] and _needs_full(s):
                full = client.fetch_full_text(pid)
                if full is None:
                    long_fail.append(pid)
            batch.append(slim_xq_post(uid, s, full))
        store.upsert_posts(batch)
        new_posts.extend(batch)
        if mode == "full":
            store.set_full_progress(uid, page, 0, now_iso())
        if mode == "incremental" and reached_old:
            break
        if page >= max_page:
            break
        page += 1

    if mode == "full" and page >= max_page:
        store.set_full_progress(uid, max_page, 1, now_iso())
    if long_fail:
        shown = long_fail[:5]
        more = "..." if len(long_fail) > 5 else ""
        print(f"[warn] user_id={uid} {len(long_fail)} 篇长文补全失败:{shown}{more}", file=sys.stderr)

    # 更新增量水位
    ts_list = [p["created_ts"] for p in new_posts if p["created_ts"]]
    if ts_list:
        store.set_last_ts(uid, max(prev_ts, max(ts_list)), now_iso())

    # ---- 评论阶段 ----
    if scope == "none":
        return len(new_posts), 0
    if mode == "full":
        target_ids = store.posts_pending_comments(uid)
    else:
        cutoff = time.time() - cfg["comment_refresh_days"] * 86400
        recent = set(store.recent_post_ids(uid, cutoff))
        target_ids = list({p["id"] for p in new_posts} | recent)

    comment_rows = 0
    for pid in target_ids:
        try:
            top = client.fetch_comments(pid, cfg["xq_max_comment_pages"])
        except RuntimeError as e:
            print(f"[warn] user_id={uid} 博文 {pid} 评论失败:{e}", file=sys.stderr)
            continue
        rows = select_xq_comments(uid, pid, top, scope)
        store.upsert_comments(rows)
        comment_rows += len(rows)
        store.mark_comments_done(pid)

    return len(new_posts), comment_rows


def _crawl_hot_rank(client: XueqiuClient, store: XueqiuStorage, cfg: dict) -> int:
    """抓当天热股榜并 upsert,返回入库行数。"""
    date_str = datetime.now().strftime("%Y%m%d")
    items = client.fetch_hot_rank(cfg["xq_hot_rank_size"], cfg["xq_hot_rank_market"])
    rows = slim_hot_rank(items, cfg["xq_hot_rank_market"], date_str)
    store.upsert_hot_rank(rows)
    return len(rows)


def _crawl_livenews(client: XueqiuClient, store: XueqiuStorage, cfg: dict) -> int:
    """按 max_id 翻页抓 7x24,遇已入库 id 或达页数上限即停,返回新增条数。"""
    max_pages = int(cfg["xq_livenews_max_pages"])
    max_id = -1
    total_new = 0
    for _ in range(max_pages):
        rows, next_max_id = client.fetch_livenews(max_id, count=15)
        if not rows:
            break
        fresh: list[dict] = []
        hit_seen = False
        for r in rows:
            if store.livenews_exists(r["id"]):
                hit_seen = True
                break
            fresh.append(r)
        store.insert_livenews(fresh)
        total_new += len(fresh)
        if hit_seen or not next_max_id:
            break
        max_id = next_max_id
    return total_new


def cmd_crawl(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("xueqiu", cfg["log_dir"])

    if obs.is_paused("xueqiu", cfg["log_dir"]):
        reason = obs.paused_reason("xueqiu", cfg["log_dir"])
        obs.log(logger, logging.WARNING, "熔断", f"检测到熔断 flag({reason}),本轮跳过")
        raise SystemExit(f"雪球抓取已熔断:{reason}。处理后删除 logs/xueqiu.paused 再跑")

    store = XueqiuStorage(cfg["db_path"])
    client = _make_client(cfg, need_login=False)  # 一期热榜/7x24 皆公开数据,走匿名免登录

    # 本轮只有两条抓取腿,阈值若大于腿数则永远到不了;有效阈值封顶为腿数,
    # 即"本轮全部腿都登录失效"就熔断(微博模块逐 uid 循环不受此限)
    LEGS = (("热股榜", _crawl_hot_rank), ("7x24 快讯", _crawl_livenews))
    threshold = min(int(cfg["auth_fail_breaker"]), len(LEGS))
    consecutive_auth_fail = 0
    hot_n = news_n = 0

    for label, fn in LEGS:
        try:
            n = fn(client, store, cfg)
            consecutive_auth_fail = 0
            if label == "热股榜":
                hot_n = n
            else:
                news_n = n
            obs.log(logger, logging.INFO, "抓取", f"{label} 入库 {n} 条")
            print(f"{label}:入库 {n} 条")
        except AuthError as e:
            consecutive_auth_fail += 1
            obs.log(logger, logging.ERROR, "抓取", f"{label} 登录失效({consecutive_auth_fail}/{threshold}):{e}")
            print(f"[error] {label} 登录失效:{e}", file=sys.stderr)
            if consecutive_auth_fail >= threshold:
                obs.set_paused("xueqiu", f"连续 {consecutive_auth_fail} 次 401/403", cfg["log_dir"])
                obs.log(logger, logging.ERROR, "熔断", f"连续 {consecutive_auth_fail} 次登录失效,已熔断停止本轮")
                print("[error] 连续登录失效超阈值,已熔断,停止本轮抓取", file=sys.stderr)
                break
        except RuntimeError as e:
            obs.log(logger, logging.ERROR, "抓取", f"{label} 抓取中断:{e}")
            print(f"[error] {label} 抓取中断:{e}", file=sys.stderr)

    hc, lc = store.count_hot_rank(), store.count_livenews()
    store.close()
    print(
        f"\n完成:本次热股榜 {hot_n} 条、7x24 新增 {news_n} 条;"
        f"库内累计热榜行 {hc}、快讯 {lc} → {cfg['db_path']}"
    )


def cmd_login(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    login_mod.login(cfg["xq_profile_dir"])


def main() -> None:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="python -m xueqiu", description="雪球单/多博主博文+评论抓取(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp_login = sub.add_parser("login", help="登录并持久化登录态")
    sp_login.set_defaults(func=cmd_login)
    sp_crawl = sub.add_parser("crawl", help="抓取")
    sp_crawl.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    sp_crawl.set_defaults(func=cmd_crawl)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
