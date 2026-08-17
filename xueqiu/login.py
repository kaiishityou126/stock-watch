"""雪球登录 & 会话:与微博 login.py 同套路(浏览器持久化 profile)。

- login():打开可见浏览器(持久化 profile),用户扫码/账号登录,登录态存磁盘。
- get_cookie_header():headless 复用同一 profile,先访问雪球主页保鲜 cookie,
  再拼出 requests 用的 Cookie 头。失效时抓取层收到 401/403 → 提示重登。

雪球登录关键 cookie 是 xq_is_login(还有 xq_a_token/xq_id_token/xq_r_token/xqat/u)。
只依赖 playwright。首次:pip install playwright && playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

HOME_URL = "https://xueqiu.com/"
LOGIN_URL = "https://xueqiu.com/#/login"


def _is_logged_in(cookies: list[dict]) -> bool:
    """雪球登录态关键 cookie:xq_is_login=1(或存在 xq_a_token)。"""
    got = {c.get("name"): c.get("value") for c in cookies}
    return got.get("xq_is_login") in ("1", 1) or bool(got.get("xq_a_token"))


def login(profile_dir: str, timeout_sec: int = 180) -> None:
    """打开浏览器让用户登录;检测到 xq_is_login/xq_a_token 即视为成功。"""
    from playwright.sync_api import sync_playwright

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir, headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HOME_URL)

        if _is_logged_in(ctx.cookies()):
            print("检测到雪球已登录状态,无需重复登录。")
            ctx.close()
            return

        page.goto(LOGIN_URL)
        print("请在弹出的浏览器里登录雪球(扫码或账号密码,最多等待 %d 秒)……" % timeout_sec)

        waited = 0
        step = 2
        while waited < timeout_sec:
            page.wait_for_timeout(step * 1000)
            waited += step
            if _is_logged_in(ctx.cookies()):
                print("雪球登录成功,登录态已保存到 %s" % profile_dir)
                ctx.close()
                return
        ctx.close()
        raise SystemExit("雪球登录超时:未检测到登录态,请重试 `python -m xueqiu login`")


def get_cookie_header(profile_dir: str) -> str:
    """headless 复用 profile,访问雪球主页保鲜后拼出 Cookie 头。"""
    from playwright.sync_api import sync_playwright

    if not Path(profile_dir).exists():
        raise SystemExit("雪球还没登录:先运行 `python -m xueqiu login`")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile_dir, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            cookies = ctx.cookies()
        finally:
            ctx.close()

    if not _is_logged_in(cookies):
        raise SystemExit("雪球登录态失效:请重新运行 `python -m xueqiu login`")

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
