"""PostgreSQL 后端：接真实数仓用的引擎。

和 SQLite 版的三处差别，都跟引擎能力有关：

  只读      SQLite 靠 `mode=ro` 打开文件；PostgreSQL 用会话级
            `default_transaction_read_only = on`（视图建完再开，否则建不了临时视图）
  超时      SQLite 靠 set_progress_handler 自己数指令；PostgreSQL 直接
            `SET statement_timeout`，到点由服务端中断
  行级权限  同样是临时视图，区别只在 PostgreSQL 的临时对象在 pg_temp 下，
            必须显式把它放进 search_path 才算「同名优先」

字段值做了归一化：NUMERIC → float，DATE/TIMESTAMP → ISO 字符串。
SQLite 里这些本来就是 REAL / TEXT，归一化之后两个引擎的结果可以直接比对。
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

from ..config import settings
from ..permissions import Scope, scoped_views
from .base import QueryResult, QueryTimeout, unique_columns

SCHEMA_PREFIX = "public."


def _normalize(value):
    """把 PostgreSQL 特有类型转成和 SQLite 一致的 Python 值。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


class PostgresBackend:
    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self):
        import psycopg  # 只有用 Postgres 时才需要这个依赖

        # autocommit：每条语句独立成事务，SET 才会作用于后面的查询本身
        return psycopg.connect(self.dsn, autocommit=True)

    def execute(self, sql: str, timeout: float | None = None,
                scope: Scope | None = None) -> QueryResult:
        import psycopg
        from psycopg import sql as pgsql

        timeout = timeout or settings.query_timeout_seconds
        conn = self._connect()
        try:
            # SET 后面不能绑参数，只能用字面量拼；拼的值全部来自我们自己的配置
            # 和规范化后的整数，不掺用户输入。
            # 时区和 SQLite 那边 date('now') 的 UTC 语义对齐，否则「最近 N 天」会差一天。
            conn.execute(
                pgsql.SQL("SET TIME ZONE {}").format(pgsql.Literal(settings.db_timezone))
            )
            for table, definition in scoped_views(scope, SCHEMA_PREFIX).items():
                conn.execute(f"CREATE TEMP VIEW {table} AS {definition}")
            # 临时视图在 pg_temp 下，必须排在 search_path 最前面才会被 FROM 命中
            conn.execute("SET search_path TO pg_temp, public")
            conn.execute(
                pgsql.SQL("SET statement_timeout = {}").format(
                    pgsql.Literal(int(timeout * 1000))
                )
            )
            # 视图已经建完，从这里开始这条连接只能读
            conn.execute("SET default_transaction_read_only = on")

            # 只统计查询本身（和 SQLite 后端对齐），建视图和 SET 不算进 query_ms
            started = time.monotonic()
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchmany(settings.query_max_rows + 1)
                columns = unique_columns(
                    [d.name for d in cur.description] if cur.description else []
                )
        except psycopg.errors.QueryCanceled as exc:
            raise QueryTimeout(f"查询超过 {timeout} 秒已中断") from exc
        finally:
            conn.close()

        normalized = [
            {col: _normalize(v) for col, v in zip(columns, row)} for row in rows
        ]
        truncated = len(normalized) > settings.query_max_rows
        return QueryResult(
            columns=columns,
            rows=normalized[: settings.query_max_rows],
            elapsed_ms=int((time.monotonic() - started) * 1000),
            truncated=truncated,
        )

    def dry_plan(self, sql: str) -> str | None:
        import psycopg

        conn = self._connect()
        try:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchall()
            return None
        except psycopg.Error as exc:
            return str(exc).strip()
        finally:
            conn.close()

    def table_stats(self) -> dict[str, int]:
        from ..schema_catalog import TABLE_DOCS

        conn = self._connect()
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
                for table in TABLE_DOCS
            }
        finally:
            conn.close()
