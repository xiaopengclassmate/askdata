"""方言切换：提示词里的引擎名和时间口径必须跟着后端走。

这组用例不需要真接 PostgreSQL——它守的是「切换时提示词会不会忘改」这件事，
而那是最容易被忽略、又最伤准确率的地方（模型照着老语法写，SQL 直接报错）。
"""

from __future__ import annotations

import pytest

from app import dialect, semantic, text2sql


@pytest.fixture
def as_postgres(monkeypatch):
    monkeypatch.setattr(dialect, "engine", lambda: "postgres")


@pytest.fixture
def as_sqlite(monkeypatch):
    # 用例里显式钉住引擎：这些断言不该受当前环境变量 DATABASE_URL 影响
    monkeypatch.setattr(dialect, "engine", lambda: "sqlite")


def test_system_prompt_names_current_engine(as_sqlite, monkeypatch):
    assert "SQLite" in dialect.system_prompt(text2sql.SYSTEM_PROMPT)
    monkeypatch.setattr(dialect, "engine", lambda: "postgres")
    assert "PostgreSQL" in dialect.system_prompt(text2sql.SYSTEM_PROMPT)
    assert "SQLite" not in dialect.system_prompt(text2sql.SYSTEM_PROMPT)


def test_sqlite_prompt_uses_sqlite_time_functions(as_sqlite):
    block = semantic.render_prompt_block()
    assert "date('now'" in block
    assert "interval" not in block


def test_postgres_prompt_uses_postgres_time_functions(as_postgres):
    block = semantic.render_prompt_block()
    assert "date('now'" not in block
    assert "date_trunc('month', now())" in block
    # 「最近 N 天」必须用日期边界，和 SQLite 的 date('now','-N days') 语义一致
    assert "current_date - N" in block


def test_postgres_prompt_swaps_examples_too(as_postgres):
    block = semantic.render_prompt_block()
    assert "interval '1 month'" in block
    assert "'-1 month'" not in block
    assert "paid_at >= current_date - 30" in block


def test_metric_definitions_are_shared_between_engines(as_postgres):
    """口径（公式、必须过滤）不随引擎变——变的只是时间写法"""
    pg = semantic.render_prompt_block()
    assert "SUM(orders.total_amount - orders.discount_amount)" in pg
    assert "orders.status <> '已取消'" in pg
    assert "退款金额" in pg
