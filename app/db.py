"""数据库访问的对外入口——具体怎么连、怎么限时、怎么只读，在 app/backends/ 里。

这一层只做三件事：转发到当前引擎、把异常类型统一、让业务代码不需要知道
底下是 SQLite 还是 PostgreSQL。换成真实数仓只要改一个环境变量（DATABASE_URL），
text2sql / main / 权限这几层一行都不用动。
"""

from __future__ import annotations

from .backends import QueryResult, QueryTimeout, get_backend
from .permissions import Scope

__all__ = ["QueryResult", "QueryTimeout", "execute_query", "dry_plan", "table_stats"]


def execute_query(
    sql: str, timeout: float | None = None, scope: Scope | None = None
) -> QueryResult:
    """执行一条已过安全网关的 SELECT，超时抛 QueryTimeout。

    scope 非空时只在查询期间对该连接生效（临时视图），由各引擎自己实现。
    """
    return get_backend().execute(sql, timeout=timeout, scope=scope)


def dry_plan(sql: str) -> str | None:
    """不执行、只做计划：返回 None 表示 SQL 能编译通过，否则返回错误信息。"""
    return get_backend().dry_plan(sql)


def table_stats() -> dict[str, int]:
    """给 /health 用：确认库存在且已灌数"""
    return get_backend().table_stats()
