"""链路里不依赖大模型的部分：选表、选图、dry-plan"""

from app import db, text2sql


def test_pick_tables_prefers_relevant():
    tables = text2sql.pick_tables("各门店的营收和退款率")
    assert "store" in tables
    assert "orders" in tables


def test_pick_tables_falls_back_to_core():
    tables = text2sql.pick_tables("随便问一句")
    assert "orders" in tables


def test_pick_tables_always_includes_orders_last():
    """orders 必须带上，且顺序按目录输出，方便模型阅读"""
    tables = text2sql.pick_tables("销售额最高的商品")
    assert tables.index("orders") > tables.index("product")


def test_chart_metric_for_single_value():
    assert text2sql.suggest_chart(["revenue"], [{"revenue": 123.0}]) == "metric"


def test_chart_line_for_time_series():
    rows = [{"d": "2026-10-01", "v": 1}, {"d": "2026-10-02", "v": 2}]
    assert text2sql.suggest_chart(["d", "v"], rows) == "line"


def test_chart_bar_for_category():
    rows = [{"store": "深圳南山店", "v": 10}, {"store": "上海徐汇店", "v": 8}]
    assert text2sql.suggest_chart(["store", "v"], rows) == "bar"


def test_chart_table_for_wide_result():
    rows = [{"a": "x", "b": 1, "c": 2}, {"a": "y", "b": 3, "c": 4}]
    assert text2sql.suggest_chart(["a", "b", "c"], rows) == "table"


def test_dry_plan_ok():
    assert db.dry_plan("SELECT COUNT(*) FROM orders") is None


def test_dry_plan_catches_typo():
    error = db.dry_plan("SELECT COUNT(*) FROM orderz")
    assert error and "orderz" in error
