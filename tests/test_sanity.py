"""结果自检：只认「无法解释的空结果」，合法值一个都不许误判"""

from app import sanity


def test_empty_with_label_literal_points_at_label():
    reason = sanity.check_result(
        ["revenue"],
        [],
        "SELECT SUM(total_amount) FROM orders WHERE channel = '线上渠道'",
    )
    assert "线上渠道" in reason
    assert "enums" in reason


def test_empty_with_only_date_literal_is_generic():
    """日期范围查不到东西是时间问题，不能回喂「取值不存在」这种错线索"""
    reason = sanity.check_result(
        ["revenue"],
        [],
        "SELECT SUM(total_amount) FROM orders WHERE paid_at >= '2026-10-01'",
    )
    assert "2026-10-01" not in reason
    assert "join" in reason


def test_empty_with_numeric_literal_is_generic():
    reason = sanity.check_result(["n"], [], "SELECT COUNT(*) FROM orders WHERE id > '100'")
    assert "100" not in reason


def test_single_null_aggregate_is_suspicious():
    reason = sanity.check_result(["revenue"], [{"revenue": None}], "SELECT SUM(x) FROM orders")
    assert "NULL" in reason


def test_null_aggregate_also_points_at_suspicious_label():
    """NULL 和「标签写错」经常同时出现，回喂时两条线索都给出来"""
    reason = sanity.check_result(
        ["revenue"],
        [{"revenue": None}],
        "SELECT SUM(total_amount) FROM orders WHERE channel = '线上渠道'",
    )
    assert "NULL" in reason
    assert "线上渠道" in reason


def test_zero_is_a_legitimate_answer():
    assert sanity.check_result(["n"], [{"n": 0}], "SELECT COUNT(*) FROM orders") == ""


def test_normal_result_is_fine():
    rows = [{"store": "深圳南山店", "revenue": 100.0}]
    assert sanity.check_result(["store", "revenue"], rows, "SELECT ...") == ""


def test_multi_row_with_null_column_is_not_flagged():
    """分组结果里某一列是 NULL 很常见（没关联上明细），硬判会误伤"""
    rows = [{"store": "A", "v": None}, {"store": "B", "v": 3}]
    assert sanity.check_result(["store", "v"], rows, "SELECT ...") == ""


def test_single_row_two_columns_null_is_not_flagged():
    assert sanity.check_result(["a", "b"], [{"a": None, "b": None}], "SELECT ...") == ""
