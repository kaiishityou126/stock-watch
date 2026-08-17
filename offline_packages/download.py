#!/usr/bin/env python3
"""在网络通畅的机器(Windows)上运行,把部署所需的离线安装包下载到本目录。

用法(在仓库根目录):
    python offline_packages/download.py                              # 下载当前平台(Windows 上就下 Windows)
    python offline_packages/download.py --target windows linux macos # 同时下载多个平台
    python offline_packages/download.py --target all                 # 下载所有平台
    python offline_packages/download.py --pyver 3.13                 # 服务器 Python 版本与本机不同时指定

目录结构:
    offline_packages/
    ├── common/      ← 跨平台纯 Python 包 (py3-none-any)
    ├── windows/     ← Windows wheel + Git.exe + Node.msi
    ├── linux/       ← Linux wheel + Node.tar.xz
    └── macos/       ← macOS wheel + Node.pkg

部署时 deploy.py 自动从 common/ + 当前平台目录安装。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COMMON_DIR = HERE / "common"

# ── 各平台配置 ──────────────────────────────────────────
TARGETS: dict[str, dict] = {
    "windows": {
        "pip_platforms": ["win_amd64"],
        "git_url": "https://github.com/git-for-windows/git/releases/download/v2.49.0.windows.1/Git-2.49.0-64-bit.exe",
        "node_url": "https://nodejs.org/dist/v22.15.0/node-v22.15.0-x64.msi",
    },
    "linux": {
        "pip_platforms": ["manylinux_2_17_x86_64", "manylinux2014_x86_64", "linux_x86_64"],
        "git_url": None,
        "node_url": "https://nodejs.org/dist/v22.15.0/node-v22.15.0-linux-x64.tar.xz",
    },
    "macos": {
        "pip_platforms": ["macosx_11_0_arm64", "macosx_10_9_x86_64"],
        "git_url": None,
        "node_url": "https://nodejs.org/dist/v22.15.0/node-v22.15.0.pkg",
    },
}

ALL_TARGETS = list(TARGETS)
TARGET_ALL = "all"


def say(msg: str) -> None:
    print(msg, flush=True)


def download_file(url: str, dest: Path) -> None:
    """下载文件,显示进度。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        say(f"  ⏭  已存在,跳过: {dest.name}")
        return
    say(f"  ↓ {url}")
    say(f"    → {dest.name}")
    urllib.request.urlretrieve(url, str(dest), _progress_hook)
    print(flush=True)
    say(f"  ✅ {dest.name} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")


def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
    if total_size > 0:
        done = block_num * block_size
        pct = min(done * 100 / total_size, 100)
        bar_len = 40
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r    [{bar}] {pct:.0f}%", end="", flush=True)


def _count_pkgs(d: Path) -> int:
    if not d.exists():
        return 0
    return len(list(d.glob("*.whl"))) + len(list(d.glob("*.tar.gz")))


def _current_target() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ── pip: 跨平台包 → common/ ─────────────────────────────

def step_pip_common() -> None:
    """下载跨平台纯 Python 包到 common/。"""
    say("\n[pip-common] 下载跨平台纯 Python 包 → common/")
    req = REPO / "requirements.txt"
    if not req.exists():
        say("❌ 找不到 requirements.txt,请在仓库根目录运行。")
        sys.exit(1)
    COMMON_DIR.mkdir(parents=True, exist_ok=True)
    # 用本机 pip download 先拿一份完整的,再把平台专属 wheel 分拣走
    cmd = [sys.executable, "-m", "pip", "download",
           "-r", str(req), "-d", str(COMMON_DIR)]
    say(f"  $ {' '.join(cmd)}")
    if subprocess.run(cmd).returncode != 0:
        say("❌ pip download 失败。检查网络后重试。")
        sys.exit(1)
    _separate_platform_wheels()
    say(f"  ✅ common/ 共 {_count_pkgs(COMMON_DIR)} 个跨平台包")


def _separate_platform_wheels() -> None:
    """把 common/ 里的平台专属 wheel 移到对应平台目录。"""
    for whl in list(COMMON_DIR.glob("*.whl")):
        name = whl.name
        if "-none-any" in name:
            continue
        if "win_amd64" in name or "win32" in name:
            _move_to_platform(whl, "windows")
        elif "manylinux" in name or "linux" in name:
            _move_to_platform(whl, "linux")
        elif "macosx" in name:
            _move_to_platform(whl, "macos")


def _move_to_platform(whl: Path, target: str) -> None:
    dst_dir = HERE / target
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / whl.name
    if not dst.exists():
        whl.rename(dst)
    else:
        whl.unlink()


# ── pip: 平台专属 wheel → <target>/ ─────────────────────

def step_pip_platform(target: str, pyver: str) -> None:
    """下载指定平台的平台专属 wheel。"""
    info = TARGETS[target]
    current_pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    dst_dir = HERE / target
    dst_dir.mkdir(parents=True, exist_ok=True)

    if _current_target() == target and pyver == current_pyver:
        say(f"\n[pip-{target}] 本机平台,平台专属包已分拣完成")
        say(f"  ✅ {target}/ 共 {_count_pkgs(dst_dir)} 个平台专属包")
        return

    say(f"\n[pip-{target}] 下载平台专属 wheel → {target}/")
    req = REPO / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "download",
           "-r", str(req), "-d", str(dst_dir)]
    for plat in info["pip_platforms"]:
        cmd += ["--platform", plat]
    cmd += ["--python-version", pyver.replace(".", ""),
            "--only-binary=:all:"]
    say(f"  $ {' '.join(cmd)}")
    if subprocess.run(cmd).returncode != 0:
        say(f"  💡 跨平台模式要求所有包都有预编译 wheel。")
        say(f"  ⚠️  {target} pip download 失败,跳过(缺 wheel 的包需在目标机联网装)。")
        return
    # 去掉和 common/ 重复的 none-any 包
    for whl in list(dst_dir.glob("*.whl")):
        if "-none-any" in whl.name and (COMMON_DIR / whl.name).exists():
            whl.unlink()
    say(f"  ✅ {target}/ 共 {_count_pkgs(dst_dir)} 个平台专属包")


# ── Git / Node ───────────────────────────────────────────

def step_git(target: str) -> None:
    url = TARGETS[target]["git_url"]
    if url is None:
        say(f"\n[git-{target}] 用系统包管理器装 git,跳过。")
        return
    say(f"\n[git-{target}] 下载 Git 安装包 → {target}/")
    dst_dir = HERE / target
    dst_dir.mkdir(parents=True, exist_ok=True)
    download_file(url, dst_dir / url.rsplit("/", 1)[-1])


def step_node(target: str) -> None:
    url = TARGETS[target]["node_url"]
    if url is None:
        say(f"\n[node-{target}] 用系统包管理器装 node,跳过。")
        return
    say(f"\n[node-{target}] 下载 Node.js 安装包 → {target}/")
    dst_dir = HERE / target
    dst_dir.mkdir(parents=True, exist_ok=True)
    download_file(url, dst_dir / url.rsplit("/", 1)[-1])


# ── CLI ──────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="下载离线部署包(分平台目录)")
    p.add_argument("--target", nargs="+", choices=ALL_TARGETS + [TARGET_ALL],
                   default=None,
                   help="目标系统,可多选;'all' 下载全部 (默认当前平台)")
    current_pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    p.add_argument("--pyver", default=current_pyver,
                   help=f"目标 Python 版本 (默认 {current_pyver})")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    targets = ALL_TARGETS if args.target and TARGET_ALL in args.target else (args.target or [_current_target()])

    say("=" * 60)
    say("  offline_packages 下载器")
    say(f"  目标平台: {', '.join(targets)}")
    say(f"  目标 Python: {args.pyver}")
    say("=" * 60)

    step_pip_common()
    for t in targets:
        step_pip_platform(t, args.pyver)
        step_git(t)
        step_node(t)

    say("\n" + "=" * 60)
    say("  全部下载完成!")
    say(f"  {'common/':12s} {_count_pkgs(COMMON_DIR)} 个跨平台包")
    for t in targets:
        d = HERE / t
        installers = sum(len(list(d.glob(p))) for p in ("*.exe", "*.msi", "*.pkg", "*.tar.xz"))
        say(f"  {t + '/':12s} {_count_pkgs(d)} 个平台包 + {installers} 个安装包")
    say("  把整个 offline_packages/ 随仓库拷到服务器即可。")
    say("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
