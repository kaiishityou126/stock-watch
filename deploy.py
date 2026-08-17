#!/usr/bin/env python3
"""首次部署引导脚本(Windows / Ubuntu / macOS 通用)。

在仓库根目录运行:python deploy.py
引导式、可中断、可重复跑:每步先探测,已完成的自动跳过。

流程:
  [0] 检测 Python ≥ 3.10(只检测,不自动装)
  [1] pip install -r requirements.txt
  [2] playwright install chromium
  [3] config.json:无则复制;按需在窗口里填 / 改 bloggers 的 uid(可多个)
  [4] 检测 Hermes,没有则按平台自动装(装失败才报错)
  [5] hermes model:没配则拉起向导(本窗口)
  [6] hermes gateway setup:没配则拉起向导(飞书/企业微信/钉钉/Telegram/Discord/Slack…)
  [7] weibo / xueqiu login:拉起,你扫码
  [8] 拷 hermes/scripts/*.py 到 Hermes 脚本目录
  [9] 建 6 条 cron(已存在跳过)
  [10] hermes gateway install
  [11] 询问是否现在跑 weibo full 全量基线
  [12] 打印验证清单

除 uid 外的所有配置项都走 weibo/cli.py 的 DEFAULTS,无需填。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
OFFLINE_DIR = REPO / "offline_packages"
PLACEHOLDER_UID = "1234567890"  # config.example.json 里的示例值,当"没填"处理
MIN_PY = (3, 10)

# Hermes 国内官方镜像(hermesagent.org.cn),自动使用国内源加速下载,install.sh 需 bash 不能 sh。
HERMES_INSTALL_WIN = "irm https://res1.hermesagent.org.cn/install.ps1 | iex"
HERMES_INSTALL_POSIX = "curl -fsSL https://res1.hermesagent.org.cn/install.sh | bash"
HERMES_INSTALL_WIN_OFFICIAL = "iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
HERMES_INSTALL_POSIX_OFFICIAL = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"

# 部署环境固定国内,依赖优先走国内镜像,失败再回退官方源。
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PLAYWRIGHT_MIRROR = "https://cdn.npmmirror.com/binaries/playwright"


# ── 通用小工具 ──────────────────────────────────────────────

def say(msg: str) -> None:
    print(msg, flush=True)


def hr(title: str) -> None:
    say("\n" + "=" * 60)
    say(f"  {title}")
    say("=" * 60)


def pause(msg: str) -> None:
    input(f"\n⏸  {msg}\n   弄完后按回车继续…")


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"{prompt} {suffix} ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def run(cmd: list[str], **kw) -> int:
    """跑命令并把 stdout/stderr 直接透传到当前终端(交互式向导靠这个)。"""
    say(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(REPO), **kw).returncode


def run_capture(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8"
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""


def hermes_scripts_dir() -> Path:
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "hermes" / "scripts"
    return Path.home() / ".hermes" / "scripts"


# ── [0] Python 版本 ────────────────────────────────────────

def step_python() -> None:
    hr("[0] 检测 Python 版本")
    cur = sys.version_info
    if (cur.major, cur.minor) >= MIN_PY:
        say(f"✅ Python {cur.major}.{cur.minor}.{cur.micro} 满足 ≥ 3.10")
        return
    say(f"❌ 当前 Python {cur.major}.{cur.minor} 过低,需要 ≥ 3.10。请先安装再重跑本脚本:")
    if IS_WIN:
        say("   Windows: winget install Python.Python.3.12  或去 https://www.python.org/downloads/")
    elif sys.platform == "darwin":
        say("   macOS:   brew install python@3.12  或去 https://www.python.org/downloads/")
    else:
        say("   Ubuntu:  sudo apt update && sudo apt install python3.12 python3.12-venv")
    sys.exit(1)


# ── [1][2] 依赖 ────────────────────────────────────────────

def _offline_platform_dir() -> str:
    if IS_WIN:
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _has_offline_wheels() -> bool:
    common = OFFLINE_DIR / "common"
    plat = OFFLINE_DIR / _offline_platform_dir()
    return common.is_dir() and any(common.glob("*.whl"))


def step_deps() -> None:
    hr("[1] 安装 Python 依赖")
    base = [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--timeout", "120"]
    if run(base + ["-i", PIP_MIRROR]) == 0:
        pass
    elif _has_offline_wheels():
        common = OFFLINE_DIR / "common"
        plat = OFFLINE_DIR / _offline_platform_dir()
        say(f"国内镜像失败,走离线包 (common/ + {_offline_platform_dir()}/)。")
        cmd = [sys.executable, "-m", "pip", "install",
               "-r", "requirements.txt", "--no-index",
               "--find-links", str(common),
               "--find-links", str(plat)]
        if run(cmd) != 0:
            say("离线安装也失败,回退官方源重试。")
            if run(base) != 0:
                say("❌ pip install 仍失败,请看上面报错(换个镜像或检查网络后重跑)。")
                sys.exit(1)
    else:
        say("国内镜像失败,回退官方源重试。")
        if run(base) != 0:
            say("❌ pip install 仍失败,请看上面报错(换个镜像或检查网络后重跑)。")
            sys.exit(1)
    say("✅ 依赖已安装")

    hr("[2] 安装 Playwright Chromium 驱动")
    pw = [sys.executable, "-m", "playwright", "install", "chromium"]
    env = {**os.environ, "PLAYWRIGHT_DOWNLOAD_HOST": PLAYWRIGHT_MIRROR}
    if run(pw, env=env) != 0:
        say("国内镜像失败,回退官方源重试。")
        if run(pw) != 0:
            say("❌ playwright install 仍失败,请看上面报错。")
            sys.exit(1)
    say("✅ Chromium 驱动已就绪")


# ── [3] config.json ────────────────────────────────────────

def _valid_uid(uid: str) -> bool:
    uid = uid.strip()
    return bool(re.fullmatch(r"\d+", uid)) and uid != PLACEHOLDER_UID


def _prompt_uids() -> list[dict]:
    """让用户输入一个或多个 uid(逗号分隔),返回 bloggers 列表。"""
    while True:
        raw = input("输入微博博主 uid(纯数字,多个用逗号隔开):").strip()
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        good, bad = [], []
        for p in parts:
            (good if _valid_uid(p) else bad).append(p)
        if bad:
            say(f"⚠️  以下不是有效 uid(需纯数字、且非示例值 {PLACEHOLDER_UID}):{', '.join(bad)}")
        if good:
            # 去重保序
            seen, uniq = set(), []
            for u in good:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)
            return [{"uid": u, "note": "微博博主"} for u in uniq]
        say("至少要有一个有效 uid,请重新输入。")


def _show_bloggers(bloggers: list[dict]) -> None:
    say("当前博主列表:")
    for i, b in enumerate(bloggers, 1):
        say(f"  {i}) {b.get('uid', '')}  {b.get('note', '')}")


def _filled_uids(bloggers: list[dict]) -> list[str]:
    return [str(b.get("uid", "")).strip() for b in bloggers if _valid_uid(str(b.get("uid", "")))]


def step_config() -> None:
    hr("[3] 配置 config.json(只填 uid,其余走默认)")
    cfg_path = REPO / "config.json"
    example = REPO / "config.example.json"

    if not cfg_path.exists():
        if not example.exists():
            say("❌ 找不到 config.example.json,无法生成配置。")
            sys.exit(1)
        shutil.copyfile(example, cfg_path)
        say("✅ 已从 config.example.json 复制出 config.json")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    bloggers = data.get("bloggers") or []

    if not _filled_uids(bloggers):
        say(f"当前 uid 还是示例值 {PLACEHOLDER_UID}(等于没填),必须填写。")
        data["bloggers"] = _prompt_uids()
        _write_config(cfg_path, data)
        _show_bloggers(data["bloggers"])
        return

    # 已填真值 → 展示并询问是否修改
    _show_bloggers(bloggers)
    if not confirm("需要修改博主列表吗?", default=False):
        say("✅ 保留现有配置")
        return
    data["bloggers"] = _edit_bloggers(bloggers)
    _write_config(cfg_path, data)
    _show_bloggers(data["bloggers"])


def _edit_bloggers(bloggers: list[dict]) -> list[dict]:
    while True:
        say("\n选择操作:[a] 追加  [b] 删除  [c] 全部重填  [q] 完成")
        op = input("> ").strip().lower()
        if op == "a":
            bloggers = bloggers + _prompt_uids()
            _show_bloggers(bloggers)
        elif op == "b":
            _show_bloggers(bloggers)
            idx = input("删除哪个?输序号:").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(bloggers):
                removed = bloggers.pop(int(idx) - 1)
                say(f"已删除 {removed.get('uid')}")
            else:
                say("序号无效")
            if not bloggers:
                say("列表空了,至少留一个,请重新填。")
                bloggers = _prompt_uids()
        elif op == "c":
            bloggers = _prompt_uids()
            _show_bloggers(bloggers)
        elif op == "q":
            if _filled_uids(bloggers):
                return bloggers
            say("列表里没有有效 uid,不能结束。")
        else:
            say("无效选择")


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say(f"✅ 已写入 {path.name}")


# ── [4] Hermes 安装 ────────────────────────────────────────

def _hermes_present() -> bool:
    if shutil.which("hermes"):
        return True
    code, _ = run_capture(["hermes", "--version"])
    return code == 0


def _find_offline(glob_pattern: str) -> Path | None:
    """在 offline_packages/<当前平台>/ 里按 glob 找最新一个匹配文件。"""
    plat_dir = OFFLINE_DIR / _offline_platform_dir()
    if not plat_dir.is_dir():
        return None
    hits = sorted(plat_dir.glob(glob_pattern))
    return hits[-1] if hits else None


def _ensure_git() -> None:
    """系统没 git 时尝试从 offline_packages/ 静默安装。"""
    if shutil.which("git"):
        return
    installer = _find_offline("Git-*.exe")
    if not installer:
        say("⚠️  系统没有 Git,offline_packages/ 里也没找到 Git-*.exe。")
        say("   Hermes 安装器会从 GitHub 下载 PortableGit,国内可能很慢。")
        say("   建议: winget install Git.Git  或放 Git 安装包到 offline_packages/")
        return
    say(f"系统没有 Git,从离线包安装: {installer.name}")
    code = run([str(installer), "/VERYSILENT", "/NORESTART", "/SP-"])
    if code != 0:
        say(f"⚠️  Git 静默安装返回 {code},Hermes 安装器会尝试自行下载。")
    else:
        say("✅ Git 离线安装完成")


def _ensure_node() -> None:
    """系统没 node 时尝试从 offline_packages/ 静默安装。"""
    if shutil.which("node"):
        return
    installer = _find_offline("node-*.msi")
    if not installer:
        say("⚠️  系统没有 Node.js,offline_packages/ 里也没找到 node-*.msi。")
        say("   Hermes 安装器会从 GitHub 下载 Node,国内可能很慢。")
        say("   建议: winget install OpenJS.NodeJS  或放 Node 安装包到 offline_packages/")
        return
    say(f"系统没有 Node.js,从离线包安装: {installer.name}")
    code = run(["msiexec", "/i", str(installer), "/qn", "/norestart"])
    if code != 0:
        say(f"⚠️  Node.js 静默安装返回 {code},Hermes 安装器会尝试自行下载。")
    else:
        say("✅ Node.js 离线安装完成")


def step_hermes_install() -> None:
    hr("[4] 检测 / 安装 Hermes")
    if _hermes_present():
        say("✅ Hermes 已安装")
        return
    # Hermes 安装器从 GitHub 拉 Git/Node,国内常卡;提前装好让它跳过。
    if IS_WIN:
        _ensure_git()
        _ensure_node()
    say("开始安装 Hermes…")
    if IS_WIN:
        code = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", HERMES_INSTALL_WIN])
        if code != 0 or not _hermes_present():
            say("国内镜像失败,回退官方源重试。")
            code = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", HERMES_INSTALL_WIN_OFFICIAL])
    else:
        code = run(["sh", "-c", HERMES_INSTALL_POSIX])
        if code != 0 or not _hermes_present():
            say("国内镜像失败,回退官方源重试。")
            code = run(["sh", "-c", HERMES_INSTALL_POSIX_OFFICIAL])
    if code != 0 or not _hermes_present():
        say("❌ Hermes 安装失败。")
        say("   若刚装好但检测不到,通常是 PATH 未刷新——新开终端再跑即可。")
        sys.exit(1)
    say("✅ Hermes 安装完成")


# ── [5][6] Hermes 交互式配置 ──────────────────────────────

def step_hermes_model() -> None:
    hr("[5] 配置 Hermes LLM 模型")
    if confirm("已经配置过 Hermes 模型了吗(hermes model)?", default=False):
        say("✅ 跳过")
        return
    say("即将拉起 `hermes model` 向导,请在窗口里选模型 / 填 API Key。")
    run(["hermes", "model"])


def step_hermes_gateway() -> None:
    hr("[6] 配置 Hermes 推送渠道")
    if confirm("已经配置过推送渠道了吗(hermes gateway setup)?", default=False):
        say("✅ 跳过")
        return
    say("Hermes 支持的推送渠道:")
    say("  国内推荐: 飞书(Feishu/Lark) · 微信(Weixin,扫码登录私信) · 企业微信(WeCom) · 钉钉(DingTalk)")
    say("  国际:     Telegram · Discord · Slack · WhatsApp · Signal · Email")
    say("即将拉起 `hermes gateway setup` 向导,在向导里选择渠道并填写配置。")
    run(["hermes", "gateway", "setup"])


# ── [7] 扫码登录 ──────────────────────────────────────────

def step_login() -> None:
    hr("[7] 微博 / 雪球扫码登录")
    if confirm("现在登录微博(会弹出浏览器扫码)?", default=True):
        run([sys.executable, "-m", "weibo", "login"])
    if confirm("现在登录雪球?", default=True):
        run([sys.executable, "-m", "xueqiu", "login"])
    say("提示:登录态存在 .wb_profile / .xq_profile,失效时重跑对应 login 即可。")


# ── [8] 拷脚本 ────────────────────────────────────────────

def step_copy_scripts() -> None:
    hr("[8] 拷贝 no-agent 脚本到 Hermes 脚本目录")
    dst = hermes_scripts_dir()
    dst.mkdir(parents=True, exist_ok=True)
    src_dir = REPO / "hermes" / "scripts"
    n = 0
    for py in sorted(src_dir.glob("stockwatch_*.py")):
        shutil.copyfile(py, dst / py.name)
        say(f"  → {py.name}")
        n += 1
    say(f"✅ 已拷贝 {n} 个脚本到 {dst}")


# ── [9] 建 cron ───────────────────────────────────────────

# (name, schedule, kind, target)  kind: "script" | "skill"
CRON_JOBS = [
    ("stockwatch-weibo", "30 9,12,15,21 * * *", "script", "stockwatch_weibo.py"),
    ("stockwatch-xueqiu", "10 8,9,11,12,14,15,17,19,20,22 * * *", "script", "stockwatch_xueqiu.py"),
    ("stockwatch-market", "35 15 * * 1-5", "script", "stockwatch_market.py"),
    ("stockwatch-daily", "40 15 * * 1-5", "skill", "hermes/SKILL.md"),
    ("stockwatch-blogger", "0 20 * * 5", "skill", "hermes/blogger-strategy.md"),
    ("stockwatch-alert", "55 8-22 * * *", "script", "stockwatch_alert.py"),
]
DELIVER_FEISHU = {"stockwatch-daily", "stockwatch-blogger", "stockwatch-alert"}


def _existing_cron_names() -> set[str]:
    code, out = run_capture(["hermes", "cron", "list"])
    if code != 0:
        return set()
    return {name for name, _, _, _ in CRON_JOBS if name in out}


def step_cron() -> None:
    hr("[9] 创建 Hermes cron 任务(已存在跳过)")
    existing = _existing_cron_names()
    workdir = str(REPO)
    for name, schedule, kind, target in CRON_JOBS:
        if name in existing:
            say(f"⏭  {name} 已存在,跳过")
            continue
        cmd = ["hermes", "cron", "create", schedule]
        if kind == "script":
            cmd += ["--no-agent", "--script", target]
        else:
            cmd += ["--skill", str(REPO / target)]
        cmd += ["--workdir", workdir]
        if name in DELIVER_FEISHU:
            cmd += ["--deliver", "feishu"]
        cmd += ["--name", name]
        if run(cmd) != 0:
            say(f"⚠️  创建 {name} 失败,请稍后手动处理。")
        else:
            say(f"✅ {name}")


# ── [10] gateway 常驻 ─────────────────────────────────────

def step_gateway_install() -> None:
    hr("[10] 启动 Hermes gateway 常驻")
    if run(["hermes", "gateway", "install"]) != 0:
        say("⚠️  gateway install 失败,请手动执行 hermes gateway install。")
    else:
        say("✅ gateway 已常驻")


# ── [11] 全量基线 ─────────────────────────────────────────

def step_full_baseline() -> None:
    hr("[11] 微博全量基线(可选)")
    say("全量抓取会翻遍历史博文 + 评论,可能几十分钟(支持断点续抓)。")
    if confirm("现在就跑一次 weibo 全量基线吗?", default=False):
        run([sys.executable, "-m", "weibo", "crawl", "--mode", "full"])
    else:
        say("跳过。之后可随时手动跑:python -m weibo crawl --mode full")


# ── [12] 验证清单 ─────────────────────────────────────────

def step_verify() -> None:
    hr("[12] 完成 —— 验证清单")
    say("请依次核对:")
    say("  1) hermes cron list                 确认 6 条已登记")
    say("  2) hermes cron run stockwatch-weibo  手动触发,看 data/app.db 有新数据")
    say("  3) hermes cron run stockwatch-daily  手动触发,看飞书收到简报")
    say("\n之后到点自动跑,无需再管。祝顺利!")


def main() -> int:
    if not (REPO / "requirements.txt").exists():
        say("请在 stock-watch 仓库根目录运行本脚本。")
        return 1
    step_python()
    step_deps()
    step_config()
    step_hermes_install()
    step_hermes_model()
    step_hermes_gateway()
    step_login()
    step_copy_scripts()
    step_cron()
    step_gateway_install()
    step_full_baseline()
    step_verify()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n已中断。可随时重跑 python deploy.py,已完成的步骤会自动跳过。")
        sys.exit(130)
