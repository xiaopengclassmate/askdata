"""SQL 方言：同一句业务问题，两个引擎的写法不一样。

模型只会照着喂给它的方言写 SQL，所以「换库」不只是换驱动——提示词里的
时间函数、示例 SQL 也必须跟着换，否则它会在 PostgreSQL 上写出 date('now')。
"""

from __future__ import annotations

from .backends import get_backend

_DIALECT_NAMES = {"sqlite": "SQLite", "postgres": "PostgreSQL"}


def engine() -> str:
    """当前后端名：sqlite / postgres"""
    return get_backend().dialect


def display_name() -> str:
    return _DIALECT_NAMES.get(engine(), engine())


def system_prompt(template: str) -> str:
    """把系统提示里的 {dialect} 换成当前引擎名。"""
    return template.replace("{dialect}", display_name())
