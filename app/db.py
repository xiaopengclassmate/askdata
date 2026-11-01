"""数据库访问：一次只读连接 + 带超时的查询执行。

「只读」是双保险：除了 sql_guard 的语法白名单，连接本身用 mode=ro 打开，
即使前面哪一层被绕过，写操作也会在数据库层直接报错。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .config import DB_PATH, settings
from .permissions import Scope, apply_scope


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    elapsed_ms: int
    truncated: bool = False


class QueryTimeout(Exception):
    pass


def _connect_readonly() -> sqlite3.Connection:
    # uri 模式 + mode=ro：数据库层强制只读
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute_query(
    sql: str, timeout: float | None = None, scope: Scope | None = None
) -> QueryResult:
    """执行一条已过安全网关的 SELECT，超时抛 QueryTimeout。

    scope 非空时，先在连接上建好受限视图，再执行——权限在数据库层生效，
    模型生成的 SQL 完全不需要知道这件事。
    """
    timeout = timeout or settings.query_timeout_seconds
    deadline = time.monotonic() + timeout
    conn = _connect_readonly()
    # 视图必须在执行前建好：TEMP 对象是连接级的，换了连接就没了
    try:
        apply_scope(conn, scope)
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


def dry_plan(sql: str) -> str | None:
    """不执行、只做计划：返回 None 表示 SQL 能 plan 通，否则返回错误信息。

    这是「先校验再执行」的关键一步——SQLite 的 EXPLAIN 只编译不跑，
    所以能在不碰数据的情况下发现表名写错、字段不存在、join 拼错。
    """
    conn = _connect_readonly()
    try:
        conn.execute(f"EXPLAIN {sql}").fetchall()
        return None
    except sqlite3.Error as exc:
        return str(exc)
    finally:
        conn.close()


def table_stats() -> dict[str, int]:
    """给 /health 用：确认库存在且已灌数"""
    conn = _connect_readonly()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
    finally:
        conn.close()
