"""可观测性:统一结构化日志 + 熔断 flag + 供 Hermes 用的 ERROR/flag 扫描。

设计取舍(见设计文档 6.3):监控靠"脚本自报(写日志/写 flag)+ Hermes 编排时读取推送",
不做独立 tail-日志守护进程。日志每行格式固定:
    时间 | 级别 | 步骤标签 | 消息
扫描按"级别字段==ERROR"判定告警,不靠正则抠中文文案 → 改文案不失效。
扫描带按文件字节偏移量,已读过的 ERROR 不重复告警。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs")
_FIELD_SEP = " | "


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StepFormatter(logging.Formatter):
    """确保每条记录都有 step 字段(缺省填 '-'),避免格式化报错。"""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "step"):
            record.step = "-"
        return super().format(record)


def get_logger(module: str, log_dir: str | Path = LOG_DIR) -> logging.Logger:
    """返回写 logs/<module>-YYYY-MM-DD.log 的 logger(同一 logger 不重复挂 handler)。"""
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{module}-{date.today().isoformat()}.log"
    logger = logging.getLogger(module)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(h, "_wb_path", None) == str(path) for h in logger.handlers):
        h = logging.FileHandler(path, encoding="utf-8")
        h._wb_path = str(path)  # type: ignore[attr-defined]
        h.setFormatter(
            _StepFormatter(
                "%(asctime)s" + _FIELD_SEP + "%(levelname)s" + _FIELD_SEP + "%(step)s" + _FIELD_SEP + "%(message)s"
            )
        )
        logger.addHandler(h)
    return logger


def log(logger: logging.Logger, level: int, step: str, msg: str) -> None:
    """带步骤标签写一条日志。level 用 logging.INFO/WARNING/ERROR。"""
    logger.log(level, msg, extra={"step": step})


# ---- 熔断 flag ----
def _flag_path(module: str, flag_dir: str | Path) -> Path:
    return Path(flag_dir) / f"{module}.paused"


def is_paused(module: str, flag_dir: str | Path = LOG_DIR) -> bool:
    return _flag_path(module, flag_dir).exists()


def set_paused(module: str, reason: str, flag_dir: str | Path = LOG_DIR) -> None:
    p = _flag_path(module, flag_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"module": module, "reason": reason, "ts": _now_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_paused(module: str, flag_dir: str | Path = LOG_DIR) -> None:
    p = _flag_path(module, flag_dir)
    if p.exists():
        p.unlink()


def paused_reason(module: str, flag_dir: str | Path = LOG_DIR) -> str | None:
    p = _flag_path(module, flag_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("reason")
    except (ValueError, OSError):
        return "(无法读取熔断原因)"


# ---- 扫描(给 Hermes/notify 用)----
def _load_offsets(state_dir: str | Path) -> dict[str, int]:
    p = Path(state_dir) / ".alert_offset.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_offsets(state_dir: str | Path, offsets: dict[str, int]) -> None:
    p = Path(state_dir) / ".alert_offset.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(offsets, ensure_ascii=False), encoding="utf-8")


def scan_issues(
    modules: list[str],
    log_dir: str | Path = LOG_DIR,
    state_dir: str | Path = LOG_DIR,
    flag_dir: str | Path = LOG_DIR,
    day: str | None = None,
) -> list[dict]:
    """扫描当天日志里未读过的 ERROR 行 + 熔断 flag,返回 issue 列表。

    每个 issue:{module, kind: 'error'|'paused', level, step, message, ts}。
    ERROR 判定只看 '级别' 字段(第 2 段),不做中文文本匹配。
    用 .alert_offset.json 记录每个日志文件已读到的字节位置,避免重复告警。
    """
    day = day or date.today().isoformat()
    offsets = _load_offsets(state_dir)
    issues: list[dict] = []

    for module in modules:
        # flag(熔断)优先
        reason = paused_reason(module, flag_dir)
        if reason is not None:
            issues.append(
                {"module": module, "kind": "paused", "level": "ERROR", "step": "熔断", "message": reason, "ts": _now_iso()}
            )
        # 日志 ERROR 行
        path = Path(log_dir) / f"{module}-{day}.log"
        if not path.exists():
            continue
        key = str(path)
        start = int(offsets.get(key, 0))
        size = path.stat().st_size
        if size < start:  # 日志被轮转/清空,从头读
            start = 0
        with path.open("r", encoding="utf-8") as f:
            f.seek(start)
            for raw in f:
                line = raw.rstrip("\n")
                parts = line.split(_FIELD_SEP, 3)
                if len(parts) < 4:
                    continue
                ts, level, step, message = parts
                if level.strip() == "ERROR":
                    issues.append(
                        {"module": module, "kind": "error", "level": "ERROR", "step": step, "message": message, "ts": ts}
                    )
            offsets[key] = f.tell()

    _save_offsets(state_dir, offsets)
    return issues
