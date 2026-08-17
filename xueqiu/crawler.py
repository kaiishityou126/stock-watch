"""抓取层:requests 直连雪球 H5/API 接口。

雪球纯 cookie、无签名/加密,比微博简单:
- 博主时间线:GET xueqiu.com/statuses/user_timeline.json?user_id=&page=&count=20
    返回 {statuses:[...], maxPage:N};用 maxPage 翻页(不要用 len<count 判停)。
- 长文补全:GET api.xueqiu.com/statuses/show.json?id=<id>(xueqiu.com 同名返回 HTML 不可用)。
- 评论:GET xueqiu.com/statuses/v3/comments.json?id=<id>&count=20&page=&reply=true&asc=false
    二级评论内嵌在每条评论的 comments 字段。

cookie 由 xueqiu.login.get_cookie_header() 提供（域 .xueqiu.com）。带重试/退避；
区分登录失效(401/403)、限流(429)、反爬(非 JSON)。created_at 是毫秒时间戳。
字段易变,报错优先对字段。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

import requests

from weibo.crawler import html_to_text, now_iso
from common.errors import AuthError, RateLimitError

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)
HOST = "https://xueqiu.com"
API = "https://api.xueqiu.com"
TIMELINE_URL = HOST + "/statuses/user_timeline.json"
SHOW_URL = API + "/statuses/show.json"
COMMENTS_URL = HOST + "/statuses/v3/comments.json"

# ---- 注意力信号端点(一期新增;已联网实测)----
HOT_RANK_URL = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
LIVENEWS_URL = HOST + "/statuses/livenews/list.json"
HQ_URL = HOST + "/hq"  # 行情页:GET 它会种匿名 token(xq_a_token 等),公开数据免登录
# 市场标签 → 接口 type 数值(沪深=10 已实测;港/美沿用映射待确认)
_HOT_MARKET_TYPE = {"沪深": 10, "港": 12, "美": 11}


def xq_ms_to_epoch(v) -> int | None:
    """雪球毫秒时间戳 → epoch 秒;解析不了返回 None。"""
    try:
        return int(int(v) / 1000)
    except (TypeError, ValueError):
        return None


def xq_ms_to_iso(v) -> str:
    ts = xq_ms_to_epoch(v)
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


def _needs_full(s: dict) -> bool:
    """雪球长文在时间线里被截断,description 常含"查看全文"/"网页链接"锚点。"""
    desc = s.get("description") or ""
    return "查看全文" in desc or "网页链接" in desc


def _xq_pics(s: dict) -> list[str]:
    urls: list[str] = []
    for pic in s.get("pic_urls") or []:
        if isinstance(pic, dict) and pic.get("url"):
            urls.append(pic["url"])
        elif isinstance(pic, str):
            urls.append(pic)
    return urls


def _to_float(v) -> float | None:
    """热度值可能是数字/字符串/缺失;转不了返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class XueqiuClient:
    def __init__(
        self,
        cookie: str | None = None,
        min_delay: float = 1.5,
        max_delay: float = 3.5,
        refresh_cookie=None,
        max_retries: int = 5,
        retry_wait: float = 3.0,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.refresh_cookie = refresh_cookie
        self._bootstrapped = False
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Origin": HOST,
                "Referer": HOST + "/",
            }
        )
        if cookie:
            self._load_cookies(cookie)

    def _load_cookies(self, cookie_str: str) -> None:
        self.session.cookies.clear()
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip(), domain=".xueqiu.com")

    def _has_token(self) -> bool:
        """session 里是否已有 xq_a_token(登录 cookie 或 bootstrap 得来的匿名 token)。"""
        return self.session.cookies.get("xq_a_token") is not None

    def _bootstrap_token(self, force: bool = False) -> bool:
        """免登录:GET 首页+行情页种匿名 token。纯 requests,不用浏览器。"""
        if self._bootstrapped and not force:
            return True
        try:
            self.session.get(HOST, timeout=20)
            self.session.get(HQ_URL, timeout=20)
        except requests.RequestException:
            return False
        self._bootstrapped = True
        return True

    def _refresh(self) -> bool:
        """token 失效时刷新:登录模式重取登录 cookie;匿名模式重新 bootstrap。"""
        if self.refresh_cookie:
            try:
                self._load_cookies(self.refresh_cookie())
                return True
            except (Exception, SystemExit):
                return False
        return self._bootstrap_token(force=True)

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get_json(self, url: str, params: dict, referer: str | None = None) -> dict:
        """带重试/退避的 GET;雪球直接返回 JSON(无 ok 包裹)。
        返回体含 error_code 视为异常;401/403 抛 AuthError,429 抛 RateLimitError。"""
        headers = {"Referer": referer} if referer else {}
        last_err: RuntimeError | None = None
        refreshed = False
        if not self._has_token():
            self._bootstrap_token()
        for attempt in range(1, self.max_retries + 1):
            self._sleep()
            refresh_eligible = False
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=20)
            except requests.RequestException as e:
                last_err = RuntimeError(f"网络异常:{e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait * attempt)
                continue

            if resp.status_code in (401, 403):
                last_err = AuthError("HTTP %d:雪球登录态失效" % resp.status_code)
                refresh_eligible = True
            elif resp.status_code == 429:
                last_err = RateLimitError("HTTP 429:雪球限流")
            elif resp.status_code != 200:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    body = resp.json()
                except ValueError:
                    last_err = AuthError(f"返回非 JSON(疑似反爬拦截):{resp.text[:120]}")
                    refresh_eligible = True
                else:
                    if isinstance(body, dict) and body.get("error_code"):
                        # 常见:400016 cookie 失效 → 当登录失效处理
                        code = str(body.get("error_code"))
                        msg = body.get("error_description") or "接口报错"
                        if code in ("400016", "1000001"):
                            last_err = AuthError(f"error_code={code}:{msg}")
                            refresh_eligible = True
                        else:
                            last_err = RuntimeError(f"error_code={code}:{msg}")
                    else:
                        return body if isinstance(body, dict) else {"_list": body}

            if refresh_eligible and not refreshed and self._refresh():
                refreshed = True
            if attempt < self.max_retries:
                time.sleep(self.retry_wait * attempt)
        raise last_err or RuntimeError("请求多次失败")

    # ---- 时间线(page/maxPage 翻页)----
    def fetch_timeline(self, user_id: str, page: int) -> tuple[list[dict], int]:
        """返回 (statuses 列表, maxPage)。"""
        params = {"user_id": user_id, "page": page, "count": 20}
        data = self._get_json(TIMELINE_URL, params, referer=f"{HOST}/u/{user_id}")
        statuses = data.get("statuses") or []
        max_page = int(data.get("maxPage") or 0)
        return statuses, max_page

    # ---- 长文补全 ----
    def fetch_full_text(self, status_id: str) -> str | None:
        try:
            data = self._get_json(SHOW_URL, {"id": status_id}, referer=f"{HOST}/")
        except RuntimeError:
            return None
        return data.get("text") or data.get("description")

    # ---- 评论(page 翻页)----
    def fetch_comments(self, status_id: str, max_pages: int) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            params = {"id": status_id, "count": 20, "page": page, "reply": "true", "asc": "false"}
            data = self._get_json(COMMENTS_URL, params, referer=f"{HOST}/")
            batch = data.get("comments") or []
            out.extend(batch)
            if not batch or page >= int(data.get("maxPage") or page):
                break
        return out

    # ---- 热股榜(每日快照)----
    def fetch_hot_rank(self, size: int, market_type: str) -> list[dict]:
        """拉热股榜,返回原始 item 列表(按热度倒序)。"""
        t = _HOT_MARKET_TYPE.get(market_type, 10)
        data = self._get_json(HOT_RANK_URL, {"size": size, "_type": t, "type": t}, referer=f"{HOST}/")
        d = data.get("data") or {}
        items = d.get("items") or d.get("list") or data.get("items") or []
        return items if isinstance(items, list) else []

    # ---- 7x24 快讯(max_id 游标翻页)----
    def fetch_livenews(self, max_id: int, count: int) -> tuple[list[dict], int]:
        """拉一页快讯,返回 (slim 行列表, 下一页 max_id)。下一页游标为 0 表示无更多。"""
        data = self._get_json(
            LIVENEWS_URL, {"since_id": -1, "max_id": max_id, "count": count}, referer=f"{HOST}/"
        )
        items = data.get("items") or data.get("list") or []
        rows = [slim_livenews(it) for it in items if isinstance(it, dict)]
        next_max_id = int(data.get("next_max_id") or data.get("next_id") or 0)
        return [r for r in rows if r["id"]], next_max_id


# ---- 字段裁剪 ----
def slim_xq_post(user_id: str, s: dict, full_text: str | None = None) -> dict:
    """雪球 status → 入库结构。full_text 为长文补全后的完整正文(优先)。"""
    user = s.get("user") or {}
    rt = s.get("retweeted_status") or {}
    return {
        "id": str(s.get("id", "")),
        "user_id": str(user_id),
        "user_name": user.get("screen_name", ""),
        "created_at": xq_ms_to_iso(s.get("created_at")),
        "created_ts": xq_ms_to_epoch(s.get("created_at")),
        "text": html_to_text(full_text or s.get("description") or s.get("text") or ""),
        "source": s.get("source", ""),
        "retweet_count": s.get("retweet_count", 0),
        "reply_count": s.get("reply_count", 0),
        "like_count": s.get("fav_count", s.get("like_count", 0)),
        "is_long_text": 1 if _needs_full(s) else 0,
        "pic_urls": json.dumps(_xq_pics(s), ensure_ascii=False),
        "retweeted_id": str(rt.get("id", "")) if rt else "",
        "retweeted_text": html_to_text(rt.get("description") or rt.get("text") or "") if rt else "",
        "raw_json": json.dumps(s, ensure_ascii=False),
        "fetched_at": now_iso(),
    }


def slim_hot_rank(items: list[dict], market_type: str, date_str: str) -> list[dict]:
    """热股榜 item 列表 → xq_hot_rank 行;rank 由列表下标 +1(接口按热度倒序)。"""
    ts = now_iso()
    rows: list[dict] = []
    for i, it in enumerate(items):
        code = str(it.get("code") or it.get("symbol") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "date": date_str,
                "symbol": code,
                "name": it.get("name") or "",
                "rank": i + 1,
                "hot_value": _to_float(it.get("value", it.get("hot_value"))),
                "market_type": market_type,
                "fetched_at": ts,
            }
        )
    return rows


def slim_livenews(it: dict) -> dict:
    """7x24 快讯 item → xq_livenews 行(HTML 裁净)。"""
    raw = it.get("text") or it.get("description") or ""
    return {
        "id": str(it.get("id", "")),
        "created_ts": xq_ms_to_epoch(it.get("created_at")),
        "created_at": xq_ms_to_iso(it.get("created_at")),
        "text": html_to_text(raw),
        "raw_json": json.dumps(it, ensure_ascii=False),
        "fetched_at": now_iso(),
    }


def slim_xq_comment(post_id: str, parent_id: str | None, c: dict) -> dict:
    user = c.get("user") or {}
    return {
        "id": str(c.get("id", "")),
        "post_id": post_id,
        "parent_id": parent_id,
        "created_at": xq_ms_to_iso(c.get("created_at")),
        "text": html_to_text(c.get("text", "")),
        "like_counts": c.get("like_count", 0),
        "user_name": user.get("screen_name", ""),
        "user_id": str(user.get("id", "")),
        "fetched_at": now_iso(),
    }


def select_xq_comments(user_id: str, post_id: str, comments: list[dict], scope: str) -> list[dict]:
    """按 scope 从评论流挑评论(按 id 去重)。

    - all:所有一级评论 + 内嵌二级评论。
    - author_only(默认):只留博主自己的评论;博主对别人的回复,连父评论一起存作上下文。
    """
    rows: dict[str, dict] = {}

    def add(parent_id: str | None, c: dict) -> None:
        r = slim_xq_comment(post_id, parent_id, c)
        if r["id"]:
            rows[r["id"]] = r

    for c in comments:
        cid = str(c.get("id", ""))
        c_uid = str((c.get("user") or {}).get("id", ""))
        subs = c.get("comments") if isinstance(c.get("comments"), list) else []
        if scope == "all":
            add(None, c)
            for s in subs:
                add(cid, s)
        else:  # author_only
            author_subs = [s for s in subs if str((s.get("user") or {}).get("id", "")) == user_id]
            if c_uid == user_id:
                add(None, c)
            if author_subs:
                add(None, c)
                for s in author_subs:
                    add(cid, s)
    return list(rows.values())
