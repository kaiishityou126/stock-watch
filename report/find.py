"""查找已生成的简报 PDF,并判定能否直接复用(A 股观察简报的快路径用)。

    python -m report.find --date 20260902

stdout 走纯 JSON(同 reader.py / render.py):

    {"exists": true, "reusable": true, "path": "<绝对路径>", "mtime_bj": "2026-09-02 18:14"}
    {"exists": false, "reusable": false, "path": null, "mtime_bj": null}

为什么需要这个模块:
- skill 的快路径原本用 `ls -t data/reports/<日期>-*.pdf | head -1`,是 Unix shell 语法,
  Windows 上不可靠;且需要 LLM 自己拼管道、自己比时间,弱模型容易出错。
- 这里把"文件在不在"和"能不能复用"都算成确定性结果,skill 只读 reusable 一个布尔值。

reusable 判定(核心):PDF 的落盘时间能反映它是"盘中快照版"还是"收盘完整版"。
- mtime 日期 > 数据日期  → 次日或更晚生成,那天数据早已定型 → 可复用
- mtime 日期 = 数据日期,且时间 >= 阈值(默认 18:10)→ 当天收盘后生成 → 可复用
- mtime 日期 = 数据日期,且时间 <  阈值            → 盘中生成,数据不全 → 不可复用
阈值取 18:10(daily cron 时间)而非 15:35 收盘口径:实测 15:35 时部分数据源尚未同步完,
18:10 是每天 cron 稳定产出完整简报的时间点。可用 config.json 的 report_reuse_after 覆盖。

**适用范围**:这套时间判定只对【A 股观察简报】成立——它依赖"行情数据某个时刻才完整"
这个前提。博主策略提炼那类 PDF 的数据(微博博文)全天增量更新,没有对应的完整时刻,
因此不走本模块(其 skill 仍用文件名匹配取最新一份)。`--slot` 参数保留是为了将来复用,
但阈值语义只对行情类简报有意义。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from common.stdio import force_utf8_stdio

_DEFAULT_REPORTS_DIR = "data/reports"
_DEFAULT_SLOT = "A股观察简报"
# 阈值缺省值与 weibo/cli.py 的 DEFAULTS["report_reuse_after"] 保持一致
_DEFAULT_REUSE_AFTER = "18:10"


def _config_value(config_path: str, key: str) -> str | None:
    """从 config.json 取单个字符串配置;文件缺失/解析失败/无该键返回 None。

    同 reader._config_db_path:只读不校验,失败静默回退默认值。
    """
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = data.get(key)
    return str(v) if v else None


def _parse_hhmm(text: str) -> tuple[int, int]:
    """把 'HH:MM' 解析成 (时, 分);格式非法抛 ValueError。"""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not m:
        raise ValueError(f"时间阈值格式应为 HH:MM,收到 {text!r}")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"时间阈值超出范围:{text!r}")
    return hh, mm


def is_reusable(data_date: str, mtime: datetime, reuse_after: str = _DEFAULT_REUSE_AFTER) -> bool:
    """判断某份 PDF 能否当作"数据完整版"复用。判定规则见模块 docstring。

    data_date 为 YYYYMMDD;mtime 是文件落盘时间(本地时区)。
    """
    file_day = mtime.strftime("%Y%m%d")
    if file_day > data_date:
        return True
    if file_day < data_date:
        # 文件比数据日期还早,不该出现(数据都没有怎么会有 PDF);保守判不可复用
        return False
    hh, mm = _parse_hhmm(reuse_after)
    return (mtime.hour, mtime.minute) >= (hh, mm)


def find_report(
    data_date: str,
    slot: str = _DEFAULT_SLOT,
    reports_dir: str = _DEFAULT_REPORTS_DIR,
    reuse_after: str = _DEFAULT_REUSE_AFTER,
) -> dict:
    """查 <reports_dir>/<data_date>-<slot>.pdf,返回 exists/reusable/path/mtime_bj。"""
    pdf = Path(reports_dir) / f"{data_date}-{slot}.pdf"
    if not pdf.is_file():
        return {"exists": False, "reusable": False, "path": None, "mtime_bj": None}
    mtime = datetime.fromtimestamp(pdf.stat().st_mtime)
    return {
        "exists": True,
        "reusable": is_reusable(data_date, mtime, reuse_after),
        "path": str(pdf.resolve()),
        "mtime_bj": mtime.strftime("%Y-%m-%d %H:%M"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="查已生成的简报 PDF 并判定可否复用(输出纯 JSON)"
    )
    ap.add_argument("--date", required=True, help="数据日期 YYYYMMDD(不是文件生成日期)")
    ap.add_argument("--slot", default=_DEFAULT_SLOT,
                    help=f"文件名槽位,默认 {_DEFAULT_SLOT};时间阈值语义只对行情类简报成立")
    ap.add_argument("--dir", default=_DEFAULT_REPORTS_DIR, help=f"PDF 目录,默认 {_DEFAULT_REPORTS_DIR}")
    ap.add_argument("--config", default="config.json", help="配置文件路径(取 report_reuse_after)")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"\d{8}", args.date):
        print(json.dumps({"error": f"--date 应为 YYYYMMDD,收到 {args.date!r}"}, ensure_ascii=False))
        return 0

    reuse_after = _config_value(args.config, "report_reuse_after") or _DEFAULT_REUSE_AFTER
    try:
        out = find_report(args.date, args.slot, args.dir, reuse_after)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 0

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
