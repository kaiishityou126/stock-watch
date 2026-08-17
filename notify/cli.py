"""告警 CLI(供 Hermes cron 调用)。

  python -m notify alert [--modules weibo,xueqiu,market]

扫 ERROR 日志 + 熔断 flag,有问题输出告警文案到 stdout（Hermes 负责投递）。

alert 用 obs.scan_issues 按级别字段判定,不靠中文正则;offset 防重复告警。
"""

from __future__ import annotations

from common import obs
from weibo.cli import load_config

DEFAULT_MODULES = ["weibo", "xueqiu", "market"]


def format_alert(issues: list[dict]) -> str | None:
    """把 issue 列表组成告警文案;无 issue 返回 None。纯函数,便于测试。"""
    if not issues:
        return None
    lines = []
    for i in issues:
        tag = "熔断" if i.get("kind") == "paused" else "错误"
        lines.append(f"[{tag}] {i.get('module')} / {i.get('step')}: {i.get('message')}")
    return "本轮检测到以下问题:\n" + "\n".join(lines)


def cmd_alert(args) -> None:
    cfg = load_config(args.config)
    modules = args.modules.split(",") if args.modules else DEFAULT_MODULES
    issues = obs.scan_issues(modules, log_dir=cfg["log_dir"], state_dir=cfg["log_dir"], flag_dir=cfg["log_dir"])
    text = format_alert(issues)
    if text is None:
        print("无告警")
        return
    print(text)  # 输出到 stdout,由 Hermes 负责投递


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m notify", description="告警扫描(扫日志/flag,有问题输出到 stdout)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp_alert = sub.add_parser("alert", help="扫日志/flag,有问题则输出告警")
    sp_alert.add_argument("--modules", default="", help="逗号分隔模块名,默认全部")
    sp_alert.set_defaults(func=cmd_alert)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
