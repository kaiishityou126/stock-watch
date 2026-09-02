"""report/render.py 单测:Markdown → PDF 渲染 + JSON 契约校验。

不依赖 LLM/Hermes/微信,纯本地:传 dict 进 render_brief,断 PDF 文件生成、
字段校验、异常路径。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from report import render


_SAMPLE = {
    "title": "测试简报",
    "content": (
        "## 总览\n\n"
        "今日市场震荡盘整。\n\n"
        "## 大盘\n\n"
        "上证 +0.12% / 深成 -0.18%。\n\n"
        "- 涨:3191\n"
        "- 跌:1821\n\n"
        "| 板块 | 涨幅 |\n|---|---|\n| 空间计算 | +3.00% |\n"
    ),
}


class TestRenderBrief(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = str(Path(self.tmp.name) / "out.pdf")

    def _can_render(self) -> bool:
        """依赖/字体齐了才能真渲;缺则跳过 PDF 内容断言,只断字段校验。"""
        try:
            render.render_brief(_SAMPLE, self.out)
            return True
        except (ImportError, RuntimeError) as e:
            if "缺少依赖" in str(e) or "找不到中文字体" in str(e):
                self.skipTest(f"环境缺依赖/字体: {e}")
            raise

    def test_happy_path_writes_pdf(self) -> None:
        if not self._can_render():
            return
        self.assertTrue(os.path.exists(self.out))
        # PDF 文件头 %PDF-
        with open(self.out, "rb") as f:
            head = f.read(5)
        self.assertEqual(head, b"%PDF-")

    def test_missing_title_raises(self) -> None:
        bad = {"content": "只有 content"}
        with self.assertRaises(ValueError) as cm:
            render.render_brief(bad, self.out)
        self.assertIn("title", str(cm.exception))

    def test_missing_content_raises(self) -> None:
        bad = {"title": "只有 title"}
        with self.assertRaises(ValueError) as cm:
            render.render_brief(bad, self.out)
        self.assertIn("content", str(cm.exception))

    def test_empty_title_raises(self) -> None:
        bad = {"title": "   ", "content": "正文"}
        with self.assertRaises(ValueError):
            render.render_brief(bad, self.out)

    def test_non_dict_payload_raises(self) -> None:
        with self.assertRaises(ValueError):
            render.render_brief("not a dict", self.out)  # type: ignore[arg-type]

    def test_wrong_type_title_raises(self) -> None:
        bad = {"title": 123, "content": "正文"}
        with self.assertRaises(ValueError):
            render.render_brief(bad, self.out)


class TestMainCLI(unittest.TestCase):
    """render.py 的 main() 走 stdin→JSON→PDF 路径,断 stdout 是合法 JSON。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = str(Path(self.tmp.name) / "cli.pdf")

    def test_stdin_json_to_pdf(self) -> None:
        import io
        from contextlib import redirect_stdout

        if render._md is None or render.pdfmetrics is None or render.BeautifulSoup is None:
            self.skipTest("缺 markdown/beautifulsoup4/reportlab 依赖")
        # 字体探测
        try:
            render._resolve_font_path()
        except RuntimeError as e:
            self.skipTest(f"缺字体: {e}")

        payload = json.dumps(_SAMPLE, ensure_ascii=False)
        # 替换 sys.stdin 整个对象(含 buffer),这样 render.main 里
        # sys.stdin.buffer.read() 才能拿到字节流
        import sys as _sys
        old_stdin = _sys.stdin
        fake_stdin = io.TextIOWrapper(io.BytesIO(payload.encode("utf-8")), encoding="utf-8")
        _sys.stdin = fake_stdin
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = render.main(["--out", self.out])
        finally:
            _sys.stdin = old_stdin
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("path"), self.out)
        self.assertTrue(os.path.exists(self.out))

    def test_bad_json_returns_error_json(self) -> None:
        import io
        from contextlib import redirect_stdout
        import sys as _sys

        old_stdin = _sys.stdin
        fake_stdin = io.TextIOWrapper(io.BytesIO(b"{not json"), encoding="utf-8")
        _sys.stdin = fake_stdin
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = render.main(["--out", self.out])
        finally:
            _sys.stdin = old_stdin
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertIn("error", out)

    def test_title_content_file_mode_to_pdf(self) -> None:
        """--title + --content-file 纯文本模式:content 是原始 markdown 文件,
        不经 JSON 解析,免转义(修复 LLM 为拼转义 JSON 而误用代码执行的问题)。
        """
        import io
        from contextlib import redirect_stdout

        if render._md is None or render.pdfmetrics is None or render.BeautifulSoup is None:
            self.skipTest("缺 markdown/beautifulsoup4/reportlab 依赖")
        try:
            render._resolve_font_path()
        except RuntimeError as e:
            self.skipTest(f"缺字体: {e}")

        content_path = Path(self.tmp.name) / "brief.md"
        content_path.write_text(_SAMPLE["content"], encoding="utf-8")

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = render.main([
                "--title", _SAMPLE["title"],
                "--content-file", str(content_path),
                "--out", self.out,
            ])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out.get("ok"))
        self.assertTrue(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
