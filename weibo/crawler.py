"""抓取层:requests 直连微博手机端 H5 接口(m.weibo.cn)。

参照 MediaCrawler 的成熟做法,统一走 m 站(结构规整、免签名、游标翻页稳、反爬松):
- 博主博文列表:GET /api/container/getIndex?type=uid&value=<uid>&containerid=107603<uid>&since_id=<游标>
    card_type==9 的 card 里带 mblog;翻页用 cardlistInfo.since_id(游标,不是页码)。
- 长文补全:GET /detail/<mid> 页面里的 `var $render_data`(与 MediaCrawler 一致,最稳),
    取完整正文;仅对 isLongText=True 的博文触发。
- 评论(热度流):GET /comments/hotflow?id=<mid>&mid=<mid>&max_id=&max_id_type=
    游标翻页;二级评论内嵌在每条评论的 comments 字段,无需额外请求。
- 登录预检:GET /api/config 的 login 字段。

cookie 由 login.get_cookie_header() 提供(m 站域)。所有 JSON 请求带有限次重试 + 退避,
区分登录失效(401/403)、限流/风控(418/429)、反爬拦截(返回非 JSON)。
"""

from __future__ import annotations

import html as _html
import json
import random
import re
import time
from datetime import datetime, timezone

import requests

from common.errors import AuthError, RateLimitError

# 手机 UA,配合 m 站访问,避免 PC UA 访问 m 站的不一致触发风控
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)
HOST = "https://m.weibo.cn"
INDEX_URL = HOST + "/api/container/getIndex"
COMMENTS_URL = HOST + "/comments/hotflow"
DETAIL_URL = HOST + "/detail/"
CONFIG_URL = HOST + "/api/config"

_TAG_RE = re.compile(r"<[^>]+>")
_RENDER_RE = re.compile(r"var \$render_data = (\[.*?\])\[0\]", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_weibo_ts(s: str) -> int | None:
    """'Wed Oct 25 14:30:00 +0800 2023' → epoch 秒;解析不了(如相对时间)返回 None。"""
    if not s:
        return None
    try:
        return int(datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        return None


def html_to_text(s: str) -> str:
    """微博正文/评论是 HTML,裁成纯文本:<br> 转换行,表情 img 保留 alt([xxx]),去标签,反转义。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r'<img[^>]*\balt="([^"]*)"[^>]*>', r"\1", s)
    s = _TAG_RE.sub("", s)
    return _html.unescape(s).strip()


def _extract_mblogs(cards: list[dict]) -> list[dict]:
    """从 getIndex 的 cards 里抽出 card_type==9 的 mblog(含 card_group 里的)。"""
    out: list[dict] = []
    for card in cards:
        if card.get("card_type") == 9 and card.get("mblog"):
            out.append(card["mblog"])
        for sub in card.get("card_group") or []:
            if sub.get("card_type") == 9 and sub.get("mblog"):
                out.append(sub["mblog"])
    return out


def _pic_urls(m: dict) -> list[str]:
    """博文配图的原图 URL 列表(供后续 OCR;这里只存链接不下载)。"""
    urls: list[str] = []
    for pic in m.get("pics") or []:
        if isinstance(pic, dict):
            url = (pic.get("large") or {}).get("url") or pic.get("url")
            if url:
                urls.append(url)
    return urls


class WeiboClient:
    def __init__(
        self,
        cookie: str,
        min_delay: float,
        max_delay: float,
        refresh_cookie=None,
        max_retries: int = 5,
        retry_wait: float = 3.0,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.refresh_cookie = refresh_cookie  # 无参可调用,返回最新 cookie 字符串(被封时刷新用)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Origin": HOST,
                "Referer": HOST + "/",
                "Content-Type": "application/json;charset=UTF-8",
            }
        )
        self._load_cookies(cookie)

    def _load_cookies(self, cookie_str: str) -> None:
        """把 Cookie 头字符串灌进 session 的 cookiejar(域 .weibo.cn),
        让 requests 自动携带并吸收接口返回的 Set-Cookie(如 M_WEIBOCN_PARAMS)。"""
        self.session.cookies.clear()
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip(), domain=".weibo.cn")

    def _refresh(self) -> bool:
        """被封/登录失效时刷新一次 cookie(借 MediaCrawler 的 432 处理思路);
        无回调或刷新失败返回 False。"""
        if not self.refresh_cookie:
            return False
        try:
            self._load_cookies(self.refresh_cookie())
        except (Exception, SystemExit):
            return False
        return True

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get_json(self, url: str, params: dict, referer: str | None = None) -> dict:
        """带重试/退避的 GET,返回 {ok,data} 里的 data。
        ok!=1 视为异常(不再静默当成“没有更多”),重试用尽仍失败则抛;
        遇登录失效/限流/反爬,先尝试刷新一次 cookie 再重试。"""
        headers = {"Referer": referer} if referer else {}
        last_err: RuntimeError | None = None
        refreshed = False
        for attempt in range(1, self.max_retries + 1):
            self._sleep()
            refresh_eligible = False  # 仅登录失效/反爬拦截才值得刷 cookie
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=20)
            except requests.RequestException as e:
                last_err = RuntimeError(f"网络异常:{e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait * attempt)
                continue

            if resp.status_code in (401, 403):
                last_err = AuthError("HTTP %d:登录态失效" % resp.status_code)
                refresh_eligible = True
            elif resp.status_code in (418, 429):
                last_err = RateLimitError("HTTP %d:被限流/风控" % resp.status_code)
            elif resp.status_code != 200:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    body = resp.json()
                except ValueError:
                    # 返回非 JSON 通常是被反爬拦截,按登录失效处理(值得刷 cookie)
                    last_err = AuthError(f"返回非 JSON(疑似反爬拦截):{resp.text[:120]}")
                    refresh_eligible = True
                else:
                    if body.get("ok") == 1:
                        return body.get("data") or {}
                    # ok!=1:可能被风控/接口报错(如评论关闭),退避重试但刷 cookie 无用
                    last_err = RuntimeError(f"ok={body.get('ok')}:{body.get('msg') or '响应异常'}")

            # 仅登录失效/反爬拦截时刷新 cookie(每次调用最多一次;限流/接口报错刷了没用)
            if refresh_eligible and not refreshed and self._refresh():
                refreshed = True
            if attempt < self.max_retries:
                time.sleep(self.retry_wait * attempt)
        raise last_err or RuntimeError("请求多次失败")

    # ---- 登录预检 ----
    def check_login(self) -> bool:
        """/api/config 的 login 字段;爬前廉价预检,避免整批白跑。"""
        try:
            data = self._get_json(CONFIG_URL, {}, referer=HOST + "/")
        except RuntimeError:
            return False
        return bool(data.get("login"))

    def prime_creator(self, uid: str) -> None:
        """先访问博主资料容器(100505),预热 M_WEIBOCN_PARAMS 等 cookie,提升列表接口稳定性;
        失败不阻断主流程(参照 MediaCrawler 先取 creator info 再列博文)。"""
        try:
            self._get_json(
                INDEX_URL,
                {"type": "uid", "value": uid, "containerid": f"100505{uid}"},
                referer=f"{HOST}/u/{uid}",
            )
        except RuntimeError:
            pass

    # ---- 博文列表(游标翻页)----
    def fetch_posts(self, uid: str, since_id: str = "") -> tuple[list[dict], str]:
        """返回 (mblog 列表, 下一个 since_id)。since_id 为空表示从头翻。"""
        params = {"type": "uid", "value": uid, "containerid": f"107603{uid}"}
        if since_id:
            params["since_id"] = since_id
        data = self._get_json(INDEX_URL, params, referer=f"{HOST}/u/{uid}")
        mblogs = _extract_mblogs(data.get("cards") or [])
        next_since = str((data.get("cardlistInfo") or {}).get("since_id") or "")
        return mblogs, next_since

    # ---- 长文补全 ----
    def fetch_full_text(self, mid: str, retries: int = 3) -> str | None:
        """抓详情页 $render_data 拿完整正文(HTML);有限次重试,全失败返回 None。"""
        referer = f"{HOST}/detail/{mid}"
        for attempt in range(1, retries + 1):
            self._sleep()
            try:
                resp = self.session.get(
                    DETAIL_URL + str(mid), headers={"Referer": referer}, timeout=20
                )
            except requests.RequestException:
                resp = None
            if resp is not None and resp.status_code == 200:
                m = _RENDER_RE.search(resp.text)
                if m:
                    try:
                        status = json.loads(m.group(1))[0].get("status") or {}
                    except (ValueError, IndexError, AttributeError):
                        status = {}
                    txt = (status.get("longText") or {}).get("longTextContent") or status.get("text")
                    if txt:
                        return txt
            if attempt < retries:
                time.sleep(self.retry_wait * attempt)
        return None

    # ---- 评论(热度流,游标翻页)----
    def fetch_comments(self, mid: str, max_pages: int) -> list[dict]:
        """一级评论(每条内嵌部分二级评论于 comments 字段)。"""
        out: list[dict] = []
        max_id = 0
        max_id_type = 0
        referer = f"{HOST}/detail/{mid}"
        for _ in range(max_pages):
            params = {"id": mid, "mid": mid, "max_id_type": max_id_type}
            if max_id:
                params["max_id"] = max_id
            data = self._get_json(COMMENTS_URL, params, referer=referer)
            batch = data.get("data") or []
            out.extend(batch)
            max_id = data.get("max_id") or 0
            max_id_type = data.get("max_id_type") or 0
            if not batch or not max_id:
                break
        return out


# ---- 字段裁剪 ----
def slim_post(uid: str, m: dict, full_text: str | None = None) -> dict:
    """m 站 mblog → 入库结构。full_text 为长文补全后的完整 HTML 正文(优先)。"""
    user = m.get("user") or {}
    retweet = m.get("retweeted_status") or {}
    title = m.get("title") or {}
    created_ts = parse_weibo_ts(m.get("created_at", ""))
    if created_ts is None:
        # 相对时间("刚刚/x分钟前")解析不出 → 用当前时间兜底,
        # 保证最新博文能进按 created_ts 圈选的当日简报(report.reader)
        created_ts = int(datetime.now(timezone.utc).timestamp())
    return {
        "id": str(m.get("id", "")),
        "uid": uid,
        "bid": m.get("bid", ""),
        "user_name": user.get("screen_name", ""),
        "created_at": m.get("created_at", ""),
        "created_ts": created_ts,
        "text": html_to_text(full_text or m.get("text", "")),
        "source": m.get("source", ""),
        "region_name": m.get("region_name", ""),
        "reposts_count": m.get("reposts_count", 0),
        "comments_count": m.get("comments_count", 0),
        "attitudes_count": m.get("attitudes_count", 0),
        "is_top": 1 if (m.get("isTop") or title.get("text") == "置顶") else 0,
        "is_long_text": 1 if m.get("isLongText") else 0,
        "pic_urls": json.dumps(_pic_urls(m), ensure_ascii=False),
        "retweeted_id": str(retweet.get("id", "")) if retweet else "",
        "retweeted_text": html_to_text(retweet.get("text", "")) if retweet else "",
        "raw_json": json.dumps(m, ensure_ascii=False),  # 原始兜底,清洗规则改了能重放
        "fetched_at": now_iso(),
    }


def slim_comment(post_id: str, parent_id: str | None, c: dict) -> dict:
    user = c.get("user") or {}
    return {
        "id": str(c.get("id", "")),
        "post_id": post_id,
        "parent_id": parent_id,
        "created_at": c.get("created_at", ""),
        "text": html_to_text(c.get("text", "")),
        "like_counts": c.get("like_count", 0),
        "user_name": user.get("screen_name", ""),
        "user_id": str(user.get("id", "")),
        "source": c.get("source", ""),
        "fetched_at": now_iso(),
    }


def select_comments(uid: str, post_id: str, top_comments: list[dict], scope: str) -> list[dict]:
    """按 comment_scope 从热度流里挑评论,返回可直接入库的行(按 id 去重)。

    - all:所有一级评论 + 内嵌二级评论。
    - author_only(默认):只留**博主自己**的评论——博主的一级评论,以及博主对别人的回复
      (内嵌二级评论);对博主的回复,连它的**父评论一起存**作上下文。
    二级评论只用热度流内嵌的那几条(best-effort,不额外翻页)。
    """
    rows: dict[str, dict] = {}

    def add(parent_id: str | None, c: dict) -> None:
        r = slim_comment(post_id, parent_id, c)
        if r["id"]:
            rows[r["id"]] = r

    for c in top_comments:
        cid = str(c.get("id", ""))
        c_uid = str((c.get("user") or {}).get("id", ""))
        subs = c.get("comments") if isinstance(c.get("comments"), list) else []
        if scope == "all":
            add(None, c)
            for s in subs:
                add(cid, s)
        else:  # author_only
            blogger_subs = [s for s in subs if str((s.get("user") or {}).get("id", "")) == uid]
            if c_uid == uid:
                add(None, c)  # 博主的一级评论
            if blogger_subs:
                add(None, c)  # 父评论(可能非博主)作上下文
                for s in blogger_subs:
                    add(cid, s)  # 博主的回复
    return list(rows.values())
