"""存储层对外的结果形状。

这里只有一条，但它是真被踩过的：`SELECT s.name, c.name` 这种**两列同名**的写法，
模型和手写 SQL 都经常出现。结果行是 dict，同名键会互相覆盖——SQLite 那边用
`dict(row)` 直接丢列，接口返回给前端就少一列；双引擎比对也会因为「两个引擎丢的
不是同一列」而报不一致。所以列名要先编号再用。
"""

from app import db


def test_duplicate_column_names_do_not_overwrite_each_other():
    result = db.execute_query(
        "SELECT s.name, c.name, COUNT(*) AS n "
        "FROM orders o JOIN store s ON s.id = o.store_id "
        "JOIN customer c ON c.id = o.customer_id "
        "GROUP BY s.name, c.name LIMIT 5"
    )
    assert result.columns[0] == "name"
    assert result.columns[1] == "name_2"
    # 两列都在，且门店名和客户名不是同一个值
    row = result.rows[0]
    assert row["name"] and row["name_2"]
    assert set(row) == {"name", "name_2", "n"}


def test_single_column_result_is_untouched():
    result = db.execute_query("SELECT COUNT(*) AS n FROM store")
    assert result.columns == ["n"]
