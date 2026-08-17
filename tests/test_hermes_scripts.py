"""Hermes 包装脚本单测:验证各脚本调对了命令、透传退出码(不真跑子进程)。

hermes/scripts/ 不是包,用 importlib 按文件路径加载。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "hermes" / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHermesScripts(unittest.TestCase):
    def _assert_script(self, name: str, expected_tail: list[str]) -> None:
        mod = _load(name)
        self.assertEqual(mod.CMD[1:], expected_tail)
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=7)
            self.assertEqual(mod.main(), 7)
            run.assert_called_once_with(mod.CMD)

    def test_weibo(self):
        self._assert_script("stockwatch_weibo", ["-m", "weibo", "crawl", "--mode", "incremental"])

    def test_xueqiu(self):
        self._assert_script("stockwatch_xueqiu", ["-m", "xueqiu", "crawl"])

    def test_market(self):
        self._assert_script("stockwatch_market", ["-m", "market", "snapshot"])

    def test_alert(self):
        self._assert_script("stockwatch_alert", ["-m", "notify", "alert"])


if __name__ == "__main__":
    unittest.main()
