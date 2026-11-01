"""行级权限：受限身份只能看到自己的门店，且这条边界在数据库层。

这些用例的意义不只是「功能对不对」，是把「越权」这件事钉死：
拿受限身份去查，结果集里绝不允许出现别的门店。
"""

import pytest

from app import db, permissions


def test_default_scope_sees_everything():
    scope = permissions.resolve("")
    assert scope.store_ids is None
    assert len(db.execute_query("SELECT id FROM store", scope=scope).rows) == 6


def test_unknown_role_is_rejected():
    with pytest.raises(permissions.ScopeError):
        permissions.resolve("ceo")


def test_single_store_scope_filters_orders():
    scope = permissions.resolve("sz-nanshan")
    mine = db.execute_query("SELECT COUNT(*) AS n FROM orders", scope=scope).rows[0]["n"]
    total = db.execute_query("SELECT COUNT(*) AS n FROM orders").rows[0]["n"]
    assert 0 < mine < total


def test_store_table_only_shows_own_store():
    scope = permissions.resolve("sz-nanshan")
    names = [r["name"] for r in db.execute_query("SELECT name FROM store", scope=scope).rows]
    assert names == ["深圳南山店"]


def test_region_scope_covers_multiple_stores():
    scope = permissions.resolve("south")
    rows = db.execute_query(
        "SELECT DISTINCT s.region FROM orders o JOIN store s ON s.id = o.store_id",
        scope=scope,
    ).rows
    assert {r["region"] for r in rows} == {"华南"}


def test_joined_tables_are_scoped_too():
    """order_item / refund 没有 store_id，必须顺着 orders 一起收窄，否则能绕过"""
    scope = permissions.resolve("sz-nanshan")

    leaked_items = db.execute_query(
        "SELECT COUNT(*) AS n FROM order_item WHERE order_id NOT IN "
        "(SELECT id FROM orders)",
        scope=scope,
    ).rows[0]["n"]
    assert leaked_items == 0

    leaked_refunds = db.execute_query(
        "SELECT COUNT(*) AS n FROM refund WHERE order_id NOT IN (SELECT id FROM orders)",
        scope=scope,
    ).rows[0]["n"]
    assert leaked_refunds == 0


def test_global_dimension_tables_are_not_filtered():
    scope = permissions.resolve("sz-nanshan")
    assert db.execute_query("SELECT COUNT(*) AS n FROM product", scope=scope).rows[0]["n"] > 0


def test_scope_does_not_allow_writes():
    scope = permissions.resolve("sz-nanshan")
    with pytest.raises(Exception):
        db.execute_query("DELETE FROM orders", scope=scope)


def test_scope_does_not_leak_into_next_connection():
    """TEMP 视图是连接级的：上一条受限连接不能污染后面的查询"""
    db.execute_query("SELECT COUNT(*) FROM orders", scope=permissions.resolve("hq"))
    names = [r["name"] for r in db.execute_query("SELECT name FROM store").rows]
    assert len(names) == 6
