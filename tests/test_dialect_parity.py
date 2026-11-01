"""同一道题、两个引擎，必须出同一个答案。

这是「换 PostgreSQL」这件事唯一有说服力的证据：不是 SQL 改得像，而是
评测集里每一条标准 SQL（方言各写一份）在两边跑出来的**结果集完全一致**。
差一行、差一分钱都要抓出来，否则换库就是把正确答案悄悄换掉。

前置条件：
  1. 起一个 PostgreSQL，灌同一份数据：python db/seed.py --url postgresql://...
  2. export TEST_POSTGRES_URL=postgresql://user:pass@host:5432/db
没设环境变量就整体跳过，不会影响只跑 SQLite 的日常测试。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

# 没装驱动（CI / 只跑 SQLite 的环境）就整组跳过，不能让收集阶段就报错
pytest.importorskip("psycopg")

from app.backends.postgres import PostgresBackend  # noqa: E402
from app.backends.sqlite import SQLiteBackend  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
PG_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="未设置 TEST_POSTGRES_URL，跳过双引擎比对"
)


def _cases() -> list[tuple[str, str, str]]:
    """(题面, SQLite 版, PostgreSQL 版)；没有 sql_pg 的题两边 SQL 相同"""
    cases: list[tuple[str, str, str]] = []
    for name in ("questions.yml", "questions-holdout.yml", "multiturn.yml"):
        data = yaml.safe_load((EVAL_DIR / name).read_text(encoding="utf-8"))
        for item in data:
            label = item.get("q") or item.get("name")
            sql = item["sql"]
            cases.append((label, sql, item.get("sql_pg") or sql))
    return cases


def _norm(rows: list[dict], columns: list[str]) -> list[tuple]:
    """按列位置比，不比列名。

    没写别名时 SQLite 用整段表达式当列名，PostgreSQL 只给 round / sum 这种函数名，
    这是两个引擎的显示习惯差异，不影响结果。行数和每行的值必须一模一样。
    """
    out = []
    for row in rows:
        vals = []
        for c in columns:
            v = row.get(c)
            if isinstance(v, float):
                v = round(v, 2)
            vals.append(v)
        out.append(tuple(vals))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


@pytest.fixture(scope="module")
def backends():
    return SQLiteBackend(), PostgresBackend(PG_URL)


def test_all_eval_sql_matches_between_engines(backends):
    sqlite_be, pg_be = backends
    cases = _cases()
    assert len(cases) >= 200, f"评测集条数不对：{len(cases)}"

    mismatched = []
    for label, sql_lite, sql_pg in cases:
        a = sqlite_be.execute(sql_lite)
        b = pg_be.execute(sql_pg)
        if len(a.columns) != len(b.columns):
            mismatched.append(f"{label}: 列数不同 {a.columns} vs {b.columns}")
            continue
        if _norm(a.rows, a.columns) != _norm(b.rows, b.columns):
            na, nb = _norm(a.rows, a.columns), _norm(b.rows, b.columns)
            first = next(
                (f"SQLite {x} / PG {y}" for x, y in zip(na, nb) if x != y), "行数或列数不同"
            )
            mismatched.append(
                f"{label}: 结果不同（SQLite {len(a.rows)} 行 / PG {len(b.rows)} 行），"
                f"首个差异 {first}"
            )

    # CI 里跑挂时，每条差异都变成一条 annotation，不用登进去翻日志才知道差在哪几题
    for m in mismatched[:10]:
        print(f"::error title=双引擎结果不一致::{m}")
    assert not mismatched, "两个引擎结果不一致：\n" + "\n".join(mismatched)


def test_rows_are_identical_at_source(backends):
    """先证明数据本身一样，再谈 SQL 结果一致——否则上面的比对没有意义

    造数用的「今天」会写进订单时间，所以两边必须在**同一天**各跑一次
    db/seed.py。隔天造的数据整体差一天，这条会先失败，比逐条 SQL 报错好定位。
    """
    sqlite_be, pg_be = backends
    for sql in (
        "SELECT COUNT(*) AS n, ROUND(SUM(total_amount - discount_amount), 2) AS rev FROM orders",
        "SELECT COUNT(*) AS n FROM order_item",
        "SELECT COUNT(*) AS n FROM refund",
        "SELECT COUNT(*) AS n FROM customer",
        "SELECT MAX(created_at) AS m FROM orders",
    ):
        assert sqlite_be.execute(sql).rows == pg_be.execute(sql).rows, (
            f"两个引擎的数据不一致：{sql}\n"
            "多半是两边不在同一天造的，重跑 db/seed.py 和 db/seed.py --url ..."
        )
