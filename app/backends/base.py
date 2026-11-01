"""存储层的公共契约。

为什么要有这一层：SQLite 够跑 demo，但真实数仓是 PostgreSQL / MySQL。
如果每条查询、每次 EXPLAIN、每道行级权限都直接写死 sqlite3 的调用方式，
换库就不是改配置，而是重写——这一层把「怎么连、怎么限时、怎么只读、
怎么按身份过滤」收成三个方法，具体怎么做交给各自的实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..permissions import Scope


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    elapsed_ms: int
    truncated: bool = False


class QueryTimeout(Exception):
    """查询超时被中断（各引擎的中断手段不同，对外抛同一个异常）。"""


class Backend(Protocol):
    # 引擎名，同时决定喂给模型的 SQL 方言说明
    dialect: str

    def execute(
        self, sql: str, timeout: float | None = None, scope: Scope | None = None
    ) -> QueryResult:
        """执行一条已过安全网关的 SELECT。

        实现方自己负责：只读、超时中断、行数上限、按 scope 过滤。
        """

    def dry_plan(self, sql: str) -> str | None:
        """只编译不执行；返回错误信息，None 表示能跑通。"""

    def table_stats(self) -> dict[str, int]:
        """每张表的行数，给 /health 自检用。"""
