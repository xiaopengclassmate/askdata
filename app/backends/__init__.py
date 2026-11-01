"""按配置挑引擎。DATABASE_URL 以 postgres:// / postgresql:// 开头就是 PostgreSQL，
否则用本地 SQLite 文件（demo 默认，不需要起任何服务）。"""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import QueryResult, QueryTimeout

__all__ = ["QueryResult", "QueryTimeout", "get_backend"]


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


@lru_cache(maxsize=1)
def get_backend():
    url = settings.database_url.strip()
    if _is_postgres(url):
        try:
            from .postgres import PostgresBackend
        except ImportError as exc:  # 配了 PG 却没装驱动，直接说清楚怎么修
            raise RuntimeError(
                "DATABASE_URL 指向 PostgreSQL，但没有安装驱动："
                "pip install -r requirements-postgres.txt"
            ) from exc

        return PostgresBackend(url)

    from .sqlite import SQLiteBackend

    return SQLiteBackend()
