"""CLI 编排。

命令:
  python -m weibo login                      扫码登录(首次 / 失效时)
  python -m weibo crawl --mode full          首次全量:翻遍每个博主全部博文+评论
  python -m weibo crawl --mode incremental   增量:只取新博文 + 刷新最近 N 天评论(默认)

博主列表、各种开关都在 config.json(从 config.example.json 复制)。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import login as login_mod
from common import obs
from .crawler import (
    WeiboClient,
    now_iso,
    select_comments,
    slim_post,
)
from common.errors import AuthError
from common.stdio import force_utf8_stdio
from .storage import Storage

DEFAULTS = {
    "max_post_pages": 2000,        # 博文列表翻页保险丝(每页约10篇);正常翻到底自然停
    "max_comment_pages": 5,        # 每篇博文评论热度流翻几页(best-effort 捞博主回复)
    "comment_refresh_days": 3,
    "enable_full_text": True,
    "comment_scope": "author_only",  # author_only(只博主评论+父评论) / all / none
    "min_delay": 1.5,
    "max_delay": 3.5,
    "db_path": "data/app.db",
    "profile_dir": ".wb_profile",
    "auth_fail_breaker": 3,        # 连续登录失效多少个博主就熔断停抓
    "log_dir": "logs",
    "xq_profile_dir": ".xq_profile",
    "xq_max_pages": 200,
    "xq_max_comment_pages": 5,
    "xq_comment_scope": "author_only",
    # ── 雪球注意力(一期)──
    "xq_hot_rank_size": 100,        # 热股榜取前 N
    "xq_hot_rank_market": "沪深",    # 沪深 / 港 / 美
    "xq_livenews_max_pages": 5,     # 7x24 单次最多翻几页
    # ── 简报交付 ──
    # 仅对【A 股观察简报】生效:当天几点后生成的 PDF 算"数据完整版"、可被快路径直接复用。
    # 取 daily cron 时间 18:10 而非 15:35 收盘口径:实测 15:35 时部分数据源尚未同步完。
    # 博主策略提炼等其他 PDF 不读此值(它们的数据没有"收盘"这个完整时刻)。
    "report_reuse_after": "18:10",
}


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"缺少配置文件 {path}:请复制 config.example.json 为 config.json 并填 uid")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    merged = {**DEFAULTS, **cfg}
    bloggers = merged.get("bloggers") or []
    uids = [str(b.get("uid", "")).strip() for b in bloggers if b.get("uid")]
    if not uids:
        raise SystemExit("config.json 的 bloggers 列表为空:至少填一个 uid")
    merged["_uids"] = uids
    return merged


def crawl_one(
    client: WeiboClient,
    store: Storage,
    uid: str,
    mode: str,
    cfg: dict,
) -> tuple[int, int]:
    """抓一个博主,返回 (新增博文数, 新增/更新评论条数)。"""
    scope = cfg["comment_scope"]

    # ---- 博文阶段 ----
    prev_max_id = store.get_last_max_id(uid) if mode == "incremental" else 0
    if mode == "full":
        since_id, full_done = store.get_full_progress(uid)
        if full_done:
            since_id = ""  # 上次已抓到底,重跑全量则从头来
    else:
        since_id = ""
    new_posts: list[dict] = []
    seen_ids: set[str] = set()
    reached_old = False
    long_fail: list[str] = []

    client.prime_creator(uid)  # 预热容器 cookie,提升列表接口稳定性

    for _ in range(cfg["max_post_pages"]):
        mblogs, since_id = client.fetch_posts(uid, since_id)
        if not mblogs:
            break
        page_has_new = False
        batch: list[dict] = []
        for m in mblogs:
            pid = str(m.get("id", ""))
            if not pid or pid in seen_ids:
                continue
            is_top = bool(m.get("isTop"))
            if mode == "incremental" and not is_top and pid.isdigit() and int(pid) <= prev_max_id:
                reached_old = True
                continue
            seen_ids.add(pid)
            full = None
            if cfg["enable_full_text"] and m.get("isLongText"):
                full = client.fetch_full_text(pid)
                if full is None:
                    long_fail.append(pid)
            batch.append(slim_post(uid, m, full))
            page_has_new = True
        store.upsert_posts(batch)  # 边翻边入库,配合断点
        new_posts.extend(batch)
        if mode == "full":
            store.set_full_progress(uid, since_id, 0, now_iso())  # checkpoint
        if mode == "incremental" and reached_old and not page_has_new:
            break
        if not since_id:
            break
    if mode == "full" and not since_id:
        store.set_full_progress(uid, "", 1, now_iso())  # 翻到底,标记完成
    if long_fail:
        shown = long_fail[:5]
        more = "..." if len(long_fail) > 5 else ""
        print(
            f"[warn] uid={uid} {len(long_fail)} 篇长文补全失败(暂存截断正文,可重跑补):{shown}{more}",
            file=sys.stderr,
        )

    # 更新增量水位(不含置顶)
    non_top = [int(p["id"]) for p in new_posts if not p["is_top"] and p["id"].isdigit()]
    if non_top:
        store.set_last_max_id(uid, max(prev_max_id, max(non_top)), now_iso())

    # ---- 评论阶段 ----
    if scope == "none":
        return len(new_posts), 0

    if mode == "full":
        # 全量:对所有"有评论且还没抓过"的博文抓评论(断点续抓靠 comments_done)
        target_ids = store.posts_pending_comments(uid)
    else:
        cutoff = time.time() - cfg["comment_refresh_days"] * 86400
        recent = set(store.recent_post_ids(uid, cutoff))
        target_ids = list({p["id"] for p in new_posts} | recent)

    comment_rows = 0
    for pid in target_ids:
        try:
            top = client.fetch_comments(pid, cfg["max_comment_pages"])
        except AuthError:
            raise  # 登录失效要交给外层熔断计数,当单篇失败吞掉会静默丢光当天评论
        except RuntimeError as e:
            print(f"[warn] uid={uid} 博文 {pid} 评论失败:{e}", file=sys.stderr)
            continue
        rows = select_comments(uid, pid, top, scope)
        store.upsert_comments(rows)
        comment_rows += len(rows)
        store.mark_comments_done(pid)

    return len(new_posts), comment_rows


def cmd_crawl(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("weibo", cfg["log_dir"])

    # 启动先看熔断 flag:已熔断则本轮跳过,等用户处理后手动清除
    if obs.is_paused("weibo", cfg["log_dir"]):
        reason = obs.paused_reason("weibo", cfg["log_dir"])
        obs.log(logger, logging.WARNING, "熔断", f"检测到熔断 flag({reason}),本轮跳过抓取")
        raise SystemExit(f"微博抓取已熔断:{reason}。处理后删除 logs/weibo.paused 再跑")

    cookie = login_mod.get_cookie_header(cfg["profile_dir"])
    store = Storage(cfg["db_path"])
    client = WeiboClient(
        cookie,
        cfg["min_delay"],
        cfg["max_delay"],
        refresh_cookie=lambda: login_mod.get_cookie_header(cfg["profile_dir"]),
    )

    if not client.check_login():
        store.close()
        obs.log(logger, logging.ERROR, "抓取", "m 站登录预检未通过")
        raise SystemExit("登录态失效(m 站预检未通过):请重新 `python -m weibo login`")

    threshold = int(cfg["auth_fail_breaker"])
    consecutive_auth_fail = 0
    total_posts = total_comments = 0
    for uid in cfg["_uids"]:
        try:
            np, nc = crawl_one(client, store, uid, args.mode, cfg)
            consecutive_auth_fail = 0
            total_posts += np
            total_comments += nc
            obs.log(logger, logging.INFO, "抓取", f"uid={uid} 新增博文 {np}、评论 {nc}")
            print(f"uid={uid}:新增博文 {np},评论 {nc} 条")
        except AuthError as e:
            consecutive_auth_fail += 1
            obs.log(logger, logging.ERROR, "抓取", f"uid={uid} 登录失效({consecutive_auth_fail}/{threshold}):{e}")
            print(f"[error] uid={uid} 登录失效:{e}", file=sys.stderr)
            if consecutive_auth_fail >= threshold:
                obs.set_paused("weibo", f"连续 {consecutive_auth_fail} 次 401/403", cfg["log_dir"])
                obs.log(logger, logging.ERROR, "熔断", f"连续 {consecutive_auth_fail} 次登录失效,已熔断停止本轮")
                print("[error] 连续登录失效超阈值,已熔断,停止本轮抓取", file=sys.stderr)
                break
        except RuntimeError as e:
            obs.log(logger, logging.ERROR, "抓取", f"uid={uid} 抓取中断:{e}")
            print(f"[error] uid={uid} 抓取中断:{e}", file=sys.stderr)

    p, c = store.counts()
    store.close()
    print(
        f"\n完成(mode={args.mode}):本次新增博文 {total_posts}、评论 {total_comments} 条;"
        f"库内累计博文 {p}、评论 {c} → {cfg['db_path']}"
    )


def cmd_login(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    login_mod.login(cfg["profile_dir"])


def main() -> None:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="python -m weibo", description="微博单/多博主博文+评论抓取(自用)")
    ap.add_argument("--config", default="config.json", help="配置文件(默认 config.json)")
    sub = ap.add_subparsers(dest="command", required=True)

    sp_login = sub.add_parser("login", help="扫码登录并持久化登录态")
    sp_login.set_defaults(func=cmd_login)

    sp_crawl = sub.add_parser("crawl", help="抓取")
    sp_crawl.add_argument(
        "--mode", choices=["full", "incremental"], default="incremental"
    )
    sp_crawl.set_defaults(func=cmd_crawl)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
