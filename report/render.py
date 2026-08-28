"""Markdown → PDF 渲染(daily-stock-brief / blogger-strategy 的下游交付环节)。

    python -m report.render [--out PATH] [--title TEXT]   (从 stdin 读 JSON {"title","content"})
    python -m report.render path/to/brief.json --out out.pdf

Hermes cron 任务在 LLM 产出 JSON 后调用,把 content 字段的 markdown 文本
渲染成 PDF,落盘到 data/reports/,再由 weixin 适配器 send_file 投递。
本模块只负责"取 JSON → 出 PDF",不查 SQLite、不决定内容。

设计要点:
- JSON 契约与 daily-stock-brief_skill.md / blogger-strategy_skill.md 完全一致:
  {"title": str, "content": str(markdown)}。两个字段都必填,缺则 raise。
- 渲染栈:markdown 库解析 md→HTML(标签层面),BeautifulSoup 切节点,
  reportlab 把每个节点画成 Flowable(标题/段落/列表/表格/代码块/引用)。
  之前用 xhtml2pdf,在 Windows 上 @font-face + file:// + .ttc 三者组合
  全部坏掉(路径解析、basepath 拼接、PermissionError 一连串),中文渲染
  全 fallback Helvetica → 方块。改用 reportlab 直接绘制后,字体注册
  (TTFont + registerFontFamily)一步到位,跨 Windows/Linux 稳。
- 字体:优先读环境变量 REPORT_FONT_PATH;否则按顺序探测 Windows 真 ttf
  (simhei.ttf 优先,因为 ttf 才稳;ttc 是字体集合,会因 subfontIndex 问题
  加载失败或选错子字体)、macOS/Linux 字体。
  找不到任何中文字体 → 抛错(不静默回退,否则 PDF 中文会变方块)。
- stdout 走纯 JSON(出错也走 {"error": ...} 且 exit 0),跟 reader.py 一致。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html import escape
from pathlib import Path

# 容许 stdlib 缺包时延迟报错(让 CLI 给出友好提示而不是 traceback)
try:
    import markdown as _md  # type: ignore
except ImportError:  # pragma: no cover
    _md = None  # type: ignore
try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:  # pragma: no cover
    pdfmetrics = None  # type: ignore
    registerFontFamily = None  # type: ignore
    TTFont = None  # type: ignore
    Paragraph = None  # type: ignore
    SimpleDocTemplate = None  # type: ignore
    Spacer = None  # type: ignore
    Table = None  # type: ignore
    TableStyle = None  # type: ignore
    colors = None  # type: ignore
    A4 = None  # type: ignore
    cm = None  # type: ignore
    ParagraphStyle = None  # type: ignore

# Windows 中文字体候选。**只接受真 ttf,不要 ttc**。xhtml2pdf 之前踩的坑:
# .ttc 是字体集合(微软雅黑 = Regular/Bold/Light 等多个子字体),TTFont 加载
# 时不传 subfontIndex 会选错子字体,中文就方块。simhei.ttf 是 9.7MB 的
# 单 ttf,Windows 自带,稳。
_DEFAULT_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",   # 真 ttf,Windows 优先
    r"C:\Windows\Fonts\msyh.ttf",     # 少数 Win10 才有,fallback
    "/System/Library/Fonts/PingFang.ttc",  # macOS(系统提供 ttc,直接交给 reportlab 选 0)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Debian/Ubuntu
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # 旧 Linux
)

_FONT_NAME = "BriefCN"  # reportlab 内部字体注册名(简报用)
_FONT_NAME_MONO = "BriefCNMono"  # 等宽字体,代码块用


def _resolve_font_path() -> str:
    """返回第一个存在的中文字体路径;找不到就 raise。"""
    env = os.environ.get("REPORT_FONT_PATH", "").strip()
    if env and os.path.exists(env):
        return env
    for cand in _DEFAULT_FONT_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise RuntimeError(
        "找不到中文字体。请设置环境变量 REPORT_FONT_PATH 指向 .ttf 文件,"
        "或把字体放到 C:\\Windows\\Fonts\\ 等系统字体目录。"
    )


def _register_fonts(font_path: str) -> None:
    """注册 reportlab 字体 + 字体族。

    中文字体: 用 TTFont 直接注册 .ttf(真 ttf 路径)。
    等宽字体: 尝试用同一个 ttf(中文 ttf 一般含等宽字形),否则 fallback。
    """
    if pdfmetrics is None or TTFont is None:
        raise ImportError("缺少依赖 reportlab,请 pip install reportlab")
    if _FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_FONT_NAME, font_path, subfontIndex=0))
        registerFontFamily(
            _FONT_NAME,
            normal=_FONT_NAME,
            bold=_FONT_NAME,
            italic=_FONT_NAME,
            boldItalic=_FONT_NAME,
        )
    # 等宽字体:reportlab 自带 Courier,只支持英文。中文部分会让 Courier
    # 自带的 fallback 机制处理(用 _FONT_NAME);这样代码块里中文也能显示,
    # 英文部分走 Courier 字形。
    if _FONT_NAME_MONO not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME_MONO, font_path, subfontIndex=0))
        except Exception:
            pass


def _make_styles() -> dict:
    """返回 reportlab ParagraphStyle 字典,简报中文排版用。"""
    return {
        "title": ParagraphStyle(
            "CNTitle", fontName=_FONT_NAME, fontSize=18, leading=22,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=10, borderPadding=4, borderWidth=0, borderColor=colors.HexColor("#333"),
        ),
        "h2": ParagraphStyle(
            "CNH2", fontName=_FONT_NAME, fontSize=14, leading=18,
            textColor=colors.HexColor("#1a1a1a"),
            spaceBefore=14, spaceAfter=6, borderPadding=2, borderWidth=0,
        ),
        "h3": ParagraphStyle(
            "CNH3", fontName=_FONT_NAME, fontSize=12, leading=15,
            textColor=colors.HexColor("#333"),
            spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "CNBody", fontName=_FONT_NAME, fontSize=10.5, leading=15.5,
            textColor=colors.HexColor("#222"),
            spaceAfter=4,
        ),
        "li": ParagraphStyle(
            "CNLi", fontName=_FONT_NAME, fontSize=10.5, leading=15,
            textColor=colors.HexColor("#222"),
            leftIndent=14, spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "CNCode", fontName=_FONT_NAME_MONO, fontSize=9, leading=12,
            textColor=colors.HexColor("#222"),
            backColor=colors.HexColor("#f4f4f4"),
            borderPadding=4, spaceBefore=4, spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "CNQuote", fontName=_FONT_NAME, fontSize=10.5, leading=15,
            textColor=colors.HexColor("#555"),
            leftIndent=10, spaceBefore=4, spaceAfter=4,
            borderPadding=4,
        ),
    }


def _md_to_html(content: str) -> str:
    """把 markdown 文本转成 HTML 字符串(标签树层面),供 BS4 解析。"""
    if _md is None:
        raise ImportError("缺少依赖 markdown,请 pip install markdown")
    return _md.markdown(
        content,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )


def _render_inline(text: str) -> str:
    """把 HTML 内联标签(**bold**/*italic*/`code`)转成 reportlab Paragraph
    能认的 <b>/<i>/<font> 子标签;同时做 HTML escape 防止注入。
    """
    if BeautifulSoup is not None:
        # 先 escape,再让 BS4 把 markdown 生成的 <strong>/<em>/<code> 重新放回
        # 但 BS4 解码后 .get_text() 会把已有标签去掉——我们用更稳的办法:
        # 假设上游 _md_to_html 已经把 ** 变成 <strong>, * 变成 <em>, ` 变成 <code>,
        # reportlab Paragraph 也认 <b>/<i>/<font>,所以只需把 strong/em/code
        # 翻译成 reportlab 标签。escape 由 Paragraph 自己处理(它接受原文,
        # 但内联标签字符串里如果有 < > 需要先 escape 再插标签)。
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup.find_all(["strong", "b"]):
            tag.name = "b"
        for tag in soup.find_all(["em", "i"]):
            tag.name = "i"
        for tag in soup.find_all("code"):
            tag.name = "font"
            tag.attrs["color"] = "#c7254e"
            tag.attrs["backColor"] = "#f9f2f4"
        for tag in soup.find_all("a"):
            # 简单把链接渲染成 "文字 (href)"
            href = tag.get("href", "")
            text_only = tag.get_text()
            tag.string = f'{text_only} ({href})'
            tag.name = "font"
            tag.attrs = {"color": "#1e6bb8"}
        return str(soup)
    # 无 BS4:兜底只 escape + 简单标签翻译
    s = escape(text)
    s = re.sub(r"<(strong|b)>(.*?)</\1>", r"<b>\2</b>", s, flags=re.DOTALL)
    s = re.sub(r"<(em|i)>(.*?)</\1>", r"<i>\2</i>", s, flags=re.DOTALL)
    s = re.sub(r"<code>(.*?)</code>", r'<font color="#c7254e" backColor="#f9f2f4">\1</font>', s, flags=re.DOTALL)
    return s


def _html_to_flowables(html: str, styles: dict) -> list:
    """把 HTML 节点树转成 reportlab Flowable 列表。"""
    if BeautifulSoup is None:
        # 无 BS4:最粗糙的兜底,按行渲染(失去表格 / 列表缩进,但不崩)
        from reportlab.platypus import Paragraph as _P
        flowables = []
        for line in html.split("\n"):
            line = line.strip()
            if not line:
                flowables.append(Spacer(1, 4))
                continue
            # 去掉所有 HTML 标签,纯文本渲染
            text = re.sub(r"<[^>]+>", "", line)
            flowables.append(_P(escape(text), styles["body"]))
        return flowables

    soup = BeautifulSoup(html, "html.parser")
    flowables: list = []

    def _para(text: str, style_key: str) -> None:
        if not text.strip():
            return
        # 列表项在 markdown 里通常以 "- " 或 "1. " 开头,但 _md 转 HTML 后是 <ul><li>。
        # 走到这里时 text 已经是 <li> 的 innerHTML,要处理内联标签
        # 文本里如果还含子 HTML 标签(如 <a>),_render_inline 会处理
        try:
            flowables.append(Paragraph(_render_inline(text), styles[style_key]))
        except Exception:
            # fallback:纯文本(任何标签错误都不让整篇失败)
            flowables.append(Paragraph(escape(re.sub(r"<[^>]+>", "", text)), styles[style_key]))

    def _walk(node) -> None:
        if hasattr(node, "name") and node.name is not None:
            name = (node.name or "").lower()
            if name == "h1":
                # 简报里 content 不会真的出现 h1(markdown # ),保险
                _para(node.get_text(), "h2")
            elif name == "h2":
                _para(node.get_text(), "h2")
            elif name == "h3":
                _para(node.get_text(), "h3")
            elif name == "p":
                _para(str(node.decode_contents()), "body")
                flowables.append(Spacer(1, 2))
            elif name == "ul":
                for li in node.find_all("li", recursive=False):
                    _para("• " + str(li.decode_contents()), "li")
                flowables.append(Spacer(1, 4))
            elif name == "ol":
                for i, li in enumerate(node.find_all("li", recursive=False), 1):
                    _para(f"{i}. " + str(li.decode_contents()), "li")
                flowables.append(Spacer(1, 4))
            elif name == "blockquote":
                inner = str(node.decode_contents()).strip()
                _para(inner, "quote")
            elif name == "pre":
                code_text = node.get_text()
                # reportlab Paragraph 在 <pre> 里会保留换行(<br/>)
                code_html = escape(code_text).replace("\n", "<br/>")
                flowables.append(Paragraph(code_html, styles["code"]))
                flowables.append(Spacer(1, 2))
            elif name == "table":
                _render_table(node, flowables, styles)
                flowables.append(Spacer(1, 4))
            elif name == "hr":
                flowables.append(Spacer(1, 8))
            else:
                # 其它标签(<div>/<span> 等):递归走子节点
                for child in node.children:
                    _walk(child)
        else:
            # 文本节点:仅当直接挂在 body 上且非空才当作段落
            text = str(node).strip()
            if text and not flowables:
                _para(text, "body")

    # 顶层 children 逐个走
    body = soup.body or soup
    for child in body.children:
        _walk(child)

    return flowables


def _render_table(table_node, flowables: list, styles: dict) -> None:
    """把 <table> 转成 reportlab Table。"""
    rows = []
    for tr in table_node.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            txt = str(cell.decode_contents()).strip()
            # reportlab Table 单元格里需要 <font> 标签而不是 <strong> 等
            txt = re.sub(r"<(strong|b)>", "<b>", txt)
            txt = re.sub(r"</(strong|b)>", "</b>", txt)
            txt = re.sub(r"<(em|i)>", "<i>", txt)
            txt = re.sub(r"</(em|i)>", "</i>", txt)
            # cell style 区分表头/普通
            is_header = (cell.name == "th")
            cell_style = ParagraphStyle(
                "TblHeader" if is_header else "TblCell",
                parent=styles["body"],
                fontName=_FONT_NAME,
                fontSize=9,
                leading=12,
                textColor=colors.white if is_header else colors.HexColor("#222"),
                backColor=colors.HexColor("#333") if is_header else None,
            )
            try:
                cells.append(Paragraph(txt, cell_style))
            except Exception:
                cells.append(Paragraph(escape(re.sub(r"<[^>]+>", "", txt)), cell_style))
        if cells:
            rows.append(cells)
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    # 补齐空 cell
    for r in rows:
        while len(r) < n_cols:
            r.append(Paragraph("", styles["body"]))
    # 估算列宽:按可用宽度均分(略偏左列宽一些,中文字符密度差不多)
    avail_w = A4[0] - 4 * cm  # 左右 margin 各 2cm
    col_widths = [avail_w / n_cols] * n_cols
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flowables.append(t)


def render_brief(payload: dict, out_path: str | os.PathLike[str]) -> None:
    """把 {title, content} JSON 渲成 PDF,落盘到 out_path。

    字段缺失或类型错 → ValueError;字体找不到 → RuntimeError;依赖没装 → ImportError。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"payload 必须是 dict,收到 {type(payload).__name__}")
    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("payload.title 必填,非空字符串")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("payload.content 必填,非空字符串")
    if _md is None:
        raise ImportError("缺少依赖 markdown,请 pip install markdown")
    if BeautifulSoup is None:
        raise ImportError("缺少依赖 beautifulsoup4,请 pip install beautifulsoup4")
    if pdfmetrics is None or SimpleDocTemplate is None:
        raise ImportError("缺少依赖 reportlab,请 pip install reportlab")

    font_path = _resolve_font_path()
    _register_fonts(font_path)
    styles = _make_styles()

    out_path = str(out_path)
    out_dir = Path(out_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    html = _md_to_html(content)
    flowables = _html_to_flowables(html, styles)

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=1.8 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
    )
    # 标题作为第一段
    title_style = styles["title"]
    final_story = [Paragraph(escape(title), title_style), Spacer(1, 6)] + flowables
    doc.build(final_story)


def main(argv: list[str] | None = None) -> int:
    from common.stdio import force_utf8_stdio  # 跟 reader.py 一致,Windows 管道不乱码

    force_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Markdown → PDF 渲染(简报/策略提炼的下游交付环节,接收 {title, content} JSON)"
    )
    ap.add_argument("input", nargs="?", default="-",
                    help="输入 JSON 文件路径,或 - 表示从 stdin 读(默认)")
    ap.add_argument("--out", default=None, help="输出 PDF 路径;不传则按 title+日期生成到 data/reports/")
    args = ap.parse_args(argv)

    try:
        if args.input == "-":
            # Windows 下 stdin 含中文时可能带 surrogate,先按 utf-8/errors 还原
            raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        else:
            raw = Path(args.input).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"JSON 解析失败: {e}"}, ensure_ascii=False))
        return 0
    except OSError as e:
        print(json.dumps({"error": f"读取输入失败: {e}"}, ensure_ascii=False))
        return 0

    # 输出路径:优先 --out,否则 data/reports/<title>-<YYYYMMDD>.pdf
    if args.out:
        out_path = args.out
    else:
        import datetime as _dt
        date_str = _dt.date.today().strftime("%Y%m%d")
        title = (payload.get("title") or "report").strip()
        safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", title)[:60] or "report"
        reports_dir = Path("data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(reports_dir / f"{date_str}-{safe}.pdf")

    try:
        render_brief(payload, out_path)
    except (ValueError, ImportError, RuntimeError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 0

    # stdout 出纯 JSON(hermes 后续步骤按这个拿路径)
    print(json.dumps({"ok": True, "path": out_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
