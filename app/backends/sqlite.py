"""SQLite 后端：demo 和本地开发用的默认引擎。"""

from __future__ import annotations

import sqlite3
import time

from ..config import DB_PATH, settings
from ..permissions import Scope, scoped_views
from .base import QueryResult, QueryTimeout

SCHEMA_PREFIX = "main."


class SQLiteBackend:
    dialect = "sqlite"

    def __init__(self, path=None) -> None:
        self.path = path or DB_PATH

    def _connect(self) -> sqlite3.Connection:
        # uri 模式 + mode=ro：数据库层强制只读
        conn = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, sql: str, timeout: float | None = None,
                scope: Scope | None = None) -> QueryResult:
        timeout = timeout or settings.query_timeout_seconds
        deadline = time.monotonic() + timeout
        conn = self._connect()
        # 视图必须在执行前建好：TEMP 对象是连接级的，换了连接就没了
        try:
            for table, definition in scoped_views(scope, SCHEMA_PREFIX).items():
                conn.execute(f"CREATE TEMP VIEW {table} AS {definition}")
        except Exception:
            conn.close()
            raise

        def _abort() -> int:
            # 返回非 0 让 SQLite 中断当前语句
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_abort, 10_000)  # 每 1 万条 VM 指令检查一次
        started = time.monotonic()
        try:
            cur = conn.execute(sql)
            rows = [dict(r) for r in cur.fetchmany(settings.query_max_rows + 1)]
            columns = [d[0] for d in cur.description] if cur.description else []
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise QueryTimeout(f"查询超过 {timeout} 秒已中断") from exc
            raise
        finally:
            conn.close()

        truncated = len(rows) > settings.query_max_rows
        return QueryResult(
            columns=columns,
            rows=rows[: settings.query_max_rows],
            elapsed_ms=int((time.monotonic() - started) * 1000),
            truncated=truncated,
        )

    def dry_plan(self, sql: str) -> str | None:
        conn = self._connect()
        try:
            conn.execute(f"EXPLAIN {sql}").fetchall()
            return None
        except sqlite3.Error as exc:
            return str(exc)
        finally:
            conn.close()

    def table_stats(self) -> dict[str, int]:
        conn = self._connect()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
        finally:
            conn.close()
