"""语义层校验：批准过的 join 放行，没批准的组合必须拦"""

from app import semantic


def test_extracts_tables():
    sql = "SELECT 1 FROM orders o JOIN store s ON s.id = o.store_id"
    assert semantic.tables_in(sql) == ["orders", "store"]


def test_approved_join_passes():
    sql = ("SELECT s.name, SUM(o.total_amount) FROM orders o "
           "JOIN store s ON s.id = o.store_id GROUP BY s.name")
    assert semantic.check(sql) == []


def test_unapproved_join_is_rejected():
    """store 和 product 之间没有批准路径，直接连上就是语义错误"""
    sql = "SELECT * FROM store s JOIN product p ON p.id = s.id"
    problems = semantic.check(sql)
    assert problems and "没有批准的关联路径" in problems[0]


def test_unknown_table_is_rejected():
    problems = semantic.check("SELECT * FROM users")
    assert problems and "不存在的表" in problems[0]


def test_single_table_has_no_join_requirement():
    assert semantic.check("SELECT COUNT(*) FROM orders") == []


def test_prompt_block_contains_metric_rules():
    block = semantic.render_prompt_block()
    assert "实收金额" in block
    assert "已取消" in block
    assert "orders.paid_at" in block
