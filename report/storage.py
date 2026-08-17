"""简报存储层(与其它组件共用 data/app.db)。

daily_report 按 report_date 唯一存档:留档、防重复推送(pushed 标志)、便于回看。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_report (
    report_date   TEXT PRIMARY KEY,
    content       TEXT,
    model         TEXT,
    source_counts TEXT,           -- JSON:各数据源当天条数,便于回看
    created_at    TEXT,
    pushed        INTEGER DEFAULT 0
);
"""


class ReportStorage:
    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def save(self, report_date: str, content: str, model: str, source_counts: str, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO daily_report (report_date, content, model, source_counts, created_at, pushed) "
                "VALUES (?,?,?,?,?,0) "
                "ON CONFLICT(report_date) DO UPDATE SET content=excluded.content, "
                "model=excluded.model, source_counts=excluded.source_counts, "
                "created_at=excluded.created_at, pushed=0",
                (report_date, content, model, source_counts, now_iso),
            )

    def get(self, report_date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_report WHERE report_date = ?", (report_date,)
        ).fetchone()
        return dict(row) if row else None

    def mark_pushed(self, report_date: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE daily_report SET pushed = 1 WHERE report_date = ?", (report_date,))
