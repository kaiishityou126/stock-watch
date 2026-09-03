"""report/find.py 单测:PDF 查找 + reusable 判定的三条边界。

不依赖真实 PDF 内容,建空文件 + os.utime 伪造 mtime 即可。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from report import find


def _touch_pdf(dirpath: Path, name: str, when: datetime) -> Path:
    """建一个空 PDF 并把 mtime 设成 when。"""
    p = dirpath / name
    p.write_bytes(b"%PDF-1.4\n")
    ts = when.timestamp()
    os.utime(p, (ts, ts))
    return p


class TestIsReusable(unittest.TestCase):
    """reusable 三条规则:次日生成 / 当天阈值后 / 当天阈值前。"""

    def test_generated_next_day_is_reusable(self) -> None:
        # 数据日期 9/2,文件 9/3 上午生成 —— 那天数据早已定型,可复用
        self.assertTrue(find.is_reusable("20260902", datetime(2026, 9, 3, 10, 0)))

    def test_same_day_after_threshold_is_reusable(self) -> None:
        self.assertTrue(find.is_reusable("20260902", datetime(2026, 9, 2, 18, 14)))

    def test_same_day_exactly_at_threshold_is_reusable(self) -> None:
        self.assertTrue(find.is_reusable("20260902", datetime(2026, 9, 2, 18, 10)))

    def test_same_day_before_threshold_not_reusable(self) -> None:
        # 盘中 14:00 生成的快照版,数据不全
        self.assertFalse(find.is_reusable("20260902", datetime(2026, 9, 2, 14, 0)))

    def test_same_day_one_minute_before_threshold_not_reusable(self) -> None:
        # 18:09 紧贴阈值下沿:锁死 >= 不被写成 >(off-by-one 会让这条误判可复用)
        self.assertFalse(find.is_reusable("20260902", datetime(2026, 9, 2, 18, 9)))

    def test_custom_threshold(self) -> None:
        self.assertFalse(find.is_reusable("20260902", datetime(2026, 9, 2, 18, 30), "19:00"))
        self.assertTrue(find.is_reusable("20260902", datetime(2026, 9, 2, 19, 5), "19:00"))

    def test_file_older_than_data_date_not_reusable(self) -> None:
        self.assertFalse(find.is_reusable("20260902", datetime(2026, 9, 1, 20, 0)))

    def test_bad_threshold_raises(self) -> None:
        with self.assertRaises(ValueError):
            find.is_reusable("20260902", datetime(2026, 9, 2, 20, 0), "18-10")


class TestFindReport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_missing_file(self) -> None:
        out = find.find_report("20260902", reports_dir=str(self.dir))
        self.assertFalse(out["exists"])
        self.assertFalse(out["reusable"])
        self.assertIsNone(out["path"])

    def test_dir_missing(self) -> None:
        # 目录不存在时 is_file() 为 False,自然走 exists:false,不该抛异常
        missing = self.dir / "no-such-subdir"
        out = find.find_report("20260902", reports_dir=str(missing))
        self.assertFalse(out["exists"])
        self.assertFalse(out["reusable"])
        self.assertIsNone(out["path"])

    def test_found_and_reusable(self) -> None:
        _touch_pdf(self.dir, "20260902-A股观察简报.pdf", datetime(2026, 9, 2, 18, 14))
        out = find.find_report("20260902", reports_dir=str(self.dir))
        self.assertTrue(out["exists"])
        self.assertTrue(out["reusable"])
        self.assertTrue(out["path"].endswith("20260902-A股观察简报.pdf"))
        self.assertEqual(out["mtime_bj"], "2026-09-02 18:14")

    def test_found_but_stale(self) -> None:
        _touch_pdf(self.dir, "20260902-A股观察简报.pdf", datetime(2026, 9, 2, 14, 0))
        out = find.find_report("20260902", reports_dir=str(self.dir))
        self.assertTrue(out["exists"])
        self.assertFalse(out["reusable"])

    def test_custom_slot(self) -> None:
        _touch_pdf(self.dir, "20260902-投资策略提炼.pdf", datetime(2026, 9, 2, 20, 5))
        out = find.find_report("20260902", slot="投资策略提炼", reports_dir=str(self.dir))
        self.assertTrue(out["exists"])
        self.assertTrue(out["reusable"])


class TestMainCLI(unittest.TestCase):
    """stdout 必须是纯 JSON(含出错路径),exit code 恒 0。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _run(self, argv: list[str]) -> dict:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = find.main(argv)
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_cli_missing_file(self) -> None:
        out = self._run(["--date", "20260902", "--dir", str(self.dir), "--config", "no-such.json"])
        self.assertFalse(out["exists"])

    def test_cli_found(self) -> None:
        _touch_pdf(self.dir, "20260902-A股观察简报.pdf", datetime(2026, 9, 2, 19, 0))
        out = self._run(["--date", "20260902", "--dir", str(self.dir), "--config", "no-such.json"])
        self.assertTrue(out["exists"])
        self.assertTrue(out["reusable"])

    def test_cli_bad_date_returns_error_json(self) -> None:
        out = self._run(["--date", "2026-09-02", "--dir", str(self.dir), "--config", "no-such.json"])
        self.assertIn("error", out)

    def test_cli_reads_threshold_from_config(self) -> None:
        cfg = self.dir / "config.json"
        cfg.write_text(json.dumps({"report_reuse_after": "20:00"}), encoding="utf-8")
        _touch_pdf(self.dir, "20260902-A股观察简报.pdf", datetime(2026, 9, 2, 19, 0))
        out = self._run(["--date", "20260902", "--dir", str(self.dir), "--config", str(cfg)])
        self.assertTrue(out["exists"])
        self.assertFalse(out["reusable"])  # 19:00 < 配置的 20:00
