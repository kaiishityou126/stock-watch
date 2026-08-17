"""抓取层分型异常。

均继承 RuntimeError,保证现有 `except RuntimeError` 仍能兜住;
新增子类只为让 CLI/熔断层区分"登录失效"与"限流",从而实现连续 401/403 熔断。
"""

from __future__ import annotations


class AuthError(RuntimeError):
    """登录态失效(HTTP 401/403 或被反爬拦截)。连续出现触发熔断。"""


class RateLimitError(RuntimeError):
    """被限流/风控(HTTP 418/429)。退避重试处理,不触发熔断。"""
