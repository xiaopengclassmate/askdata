"""安全网关的单测 —— 这一层的价值全在"该拒的必须拒"，所以要逐条钉死"""

import pytest

from app import sql_guard


@pytest.mark.parametrize("sql", [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET total_amount = 0",
    "INSERT INTO orders (id) VALUES (1)",
    "CREATE TABLE x (id int)",
    "ALTER TABLE orders ADD COLUMN x TEXT",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(orders)",
])
def test_rejects_non_select(sql):
    result = sql_guard.check(sql)
    assert not result.ok


@pytest.mark.parametrize("sql", [
    "SELECT 1; DELETE FROM orders",
    "SELECT * FROM orders; DROP TABLE orders;",
    "SELECT 1 /* ; */ ; SELECT 2",
])
def test_rejects_multiple_statements(sql):
    result = sql_guard.check(sql)
    assert not result.ok
    assert "单条" in result.reason


@pytest.mark.parametrize("sql", [
    "SELECT name FROM store",
    "WITH t AS (SELECT id FROM orders) SELECT COUNT(*) FROM t",
    "  select * from orders  ",
])
def test_accepts_readonly_queries(sql):
    assert sql_guard.check(sql).ok


def test_appends_limit_when_missing():
    result = sql_guard.check("SELECT * FROM orders")
    assert result.sql.endswith("LIMIT 200")


def test_keeps_existing_limit():
    result = sql_guard.check("SELECT * FROM orders LIMIT 5")
    assert result.sql.upper().count("LIMIT") == 1
    assert "LIMIT 5" in result.sql


def test_caps_oversized_limit():
    result = sql_guard.check("SELECT * FROM orders LIMIT 99999")
    assert "LIMIT 1000" in result.sql


def test_keyword_in_column_name_is_allowed():
    """status / created_at 这类字段名不能被误伤"""
    assert sql_guard.check("SELECT status, created_at FROM orders").ok
