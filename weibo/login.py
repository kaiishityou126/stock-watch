"""登录 & 会话:借鉴 MediaCrawler 的"登录态浏览器持久化"思路。

- login():打开可见浏览器(持久化 profile),用户手机扫码登录,登录态存到磁盘。
- get_cookies():以 headless 复用同一 profile,取出当前 cookie(自动保鲜),
  供 crawler 用 requests 直连接口。cookie 失效时抓取层会收到 401/403 → 提示重登。

只依赖 playwright。首次用:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import logging
from pathlib import Path

from common import obs

LOGIN_URL = "https://weibo.com/login.php"
HOME_URL = "https://weibo.com/"
M_HOME_URL = "https://m.weibo.cn/"  # 抓取走 m 站,需要 m.weibo.cn 域的 cookie
M_CONFIG_URL = M_HOME_URL + "api/config"


def _is_logged_in(cookies: list[dict]) -> bool:
    """微博登录态的关键 cookie 是 SUB。"""
    return any(c.get("name") == "SUB" and c.get("value") for c in cookies)


def _passport_logged_in(page) -> bool:
    """权威校验:m 站 /api/config 的 login 字段(与抓取层预检口径一致)。
    微博给游客也发 SUB cookie,只查 cookie 会误判为已登录,必须问服务端。"""
    try:
        resp = page.request.get(M_CONFIG_URL, headers={"Referer": M_HOME_URL})
        if not resp.ok:
            return False
        return bool((resp.json().get("data") or {}).get("login"))
    except Exception:
        return False


def login(profile_dir: str, timeout_sec: int = 180) -> None:
    """打开浏览器让用户扫码;检测到 SUB cookie 即视为成功。"""
    from playwright.sync_api import sync_playwright

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir, headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(M_HOME_URL, wait_until="domcontentloaded")

        # 权威判断:问服务端是否真登录,游客态 SUB cookie 骗不过它
        if _passport_logged_in(page):
            print("检测到已登录状态,无需重复扫码。")
            ctx.close()
            return

        ctx.clear_cookies()  # 先清游客/过期 cookie,再进登录页才会显示二维码而非跳回主页
        page.goto(LOGIN_URL, wait_until="networkidle")  # 等二维码 JS 渲染完
        print("请在弹出的浏览器里用微博 App 扫码登录(最多等待 %d 秒)……" % timeout_sec)

        waited = 0
        step = 2
        while waited < timeout_sec:
            page.wait_for_timeout(step * 1000)
            waited += step
            if _passport_logged_in(page):
                print("登录成功,登录态已保存到 %s" % profile_dir)
                page.goto(M_HOME_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # SSO 到 m 站,种下 m.weibo.cn 域 cookie
                ctx.close()
                return
        ctx.close()
        raise SystemExit("登录超时:未检测到登录态,请重试 `python -m weibo login`")


def _log_login_error(msg: str) -> None:
    """登录态问题落到 ERROR 日志,供 notify alert 扫描推送。

    SystemExit 只进 stderr:cron 的 no-agent 投递是本地,alert 又只认日志
    第 2 段字段——不写这条的话,微博整轮空跑用户在微信上毫无感知。
    """
    try:
        obs.log(obs.get_logger("weibo"), logging.ERROR, "登录", msg)
    except Exception:
        pass  # 日志失败不阻断原报错抛出


def get_cookie_header(profile_dir: str) -> str:
    """headless 复用 profile,拼出 requests 用的 Cookie 头字符串。"""
    # profile 缺失在加载 playwright 前就拦下:报错更快,也让无 playwright 的测试环境可测本分支
    if not Path(profile_dir).exists():
        msg = "还没登录:先运行 `python -m weibo login` 扫码"
        _log_login_error(msg)
        raise SystemExit(msg)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile_dir, headless=True)
        try:
            # 访问一次 m 站主页,确保 m.weibo.cn 域 cookie 处于最新
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(M_HOME_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            cookies = ctx.cookies()
        finally:
            ctx.close()

    if not _is_logged_in(cookies):
        msg = "登录态失效:请重新运行 `python -m weibo login` 扫码"
        _log_login_error(msg)
        raise SystemExit(msg)

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
