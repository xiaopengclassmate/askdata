"""评测集自检：不调模型，只验「标准答案本身对不对」。

扩题时最容易犯的错不是模型答错，是标准答案是错的：少写一个 status 过滤、
join 写漏一张表、时间窗口写早一天——这种错会让评测永远判错，而且看不出来。
这个脚本把每道题的标准 SQL 真跑一遍，用几条硬性规则挑毛病：

  · 语法/字段错          —— 跑不起来，直接报
  · 0 行 / 单值 NULL     —— 正确答案不该是空的（app/sanity.py 也会把它判可疑）
  · 缺 cat / batch       —— 归因和分批次统计要用
  · 题干重复             —— 同一句话出现两次，等于白占一个名额
  · 时间题没给 sql_pg    —— 标准 SQL 里出现 SQLite 专有函数却没 PG 版

用法：
    python eval/check_questions.py                       # 两个文件都查
    python eval/check_questions.py --questions questions.yml
    TEST_POSTGRES_URL=... python eval/check_questions.py --parity   # 顺带比两引擎结果集

--parity 需要同步好两边数据（同一天灌），否则时间相关的题会整体差一天。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

from app import dialect  # noqa: E402
from app.backends import get_backend  # noqa: E402

CATEGORIES = {
    "time", "metric", "group", "topn", "ratio",
    "join", "negate", "snapshot", "customer", "compare",
}

# SQLite 专有写法：出现这些就该配一份 sql_pg
_SQLITE_ONLY = ("date('now'", "strftime(", "date(paid_at")

_LIMIT = re.compile(r"LIMIT\s+(\d+)\s*$", re.I)


def _norm(columns: list[str], rows: list[dict]) -> list[tuple]:
    """按列位置取值、数值留 2 位、**排序后再比**。

    排序是必须的：GROUP BY 的行序在两个引擎里本来就不保证一致，
    不排序会把「顺序不同」误报成「结果不同」。
    """
    out = []
    for row in rows:
        vals = []
        for c in columns:
            v = row.get(c)
            vals.append(round(v, 2) if isinstance(v, float) else v)
        out.append(tuple(vals))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def _run(sql: str, backend) -> tuple[list[str], list[dict]]:
    result = backend.execute(sql, timeout=30, scope=None)
    return result.columns, result.rows


def _tie_at_cut(sql: str, columns: list[str], rows: list[dict], backend) -> str:
    """LIMIT 边界上有没有并列：有的话这题**没有唯一答案**，两个引擎可以各挑一行。

    多取一行就能看出来：第 k 名和第 k+1 名的指标列相同，说明名次是靠运气排的。
    只看得到一列（问「哪个」）时没法比，这种得多半人工看一眼。
    """
    match = _LIMIT.search(sql)
    if not match or len(columns) < 2:
        return ""
    k = int(match.group(1))
    try:
        _, more = _run(sql[: match.start()] + f"LIMIT {k + 1}", backend)
    except Exception:
        return ""
    if len(more) <= k:
        return ""
    last = columns[-1]
    return (
        f"第 {k} 名和第 {k + 1} 名的「{last}」都是 {more[k - 1].get(last)}，"
        "答案不唯一"
        if more[k - 1].get(last) == more[k].get(last)
        else ""
    )


def check_file(path: Path, backend, compare_engine) -> list[str]:
    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    seen: dict[str, int] = {}

    for i, item in enumerate(items, 1):
        tag = f"{path.name}[{i}] {item.get('q', '<无题干>')}"
        for field in ("q", "sql", "cat", "batch"):
            if not item.get(field):
                problems.append(f"{tag}：缺字段 {field}")
        if item.get("cat") and item["cat"] not in CATEGORIES:
            problems.append(f"{tag}：cat={item['cat']} 不在题型定义里")
        if item.get("q") in seen:
            problems.append(f"{tag}：题干和第 {seen[item['q']]} 条重复")
        seen.setdefault(item.get("q", ""), i)

        sql = item.get("sql")
        if not sql:
            continue
        if any(fn in sql for fn in _SQLITE_ONLY) and not item.get("sql_pg"):
            problems.append(f"{tag}：用了 SQLite 专有日期函数，但没给 sql_pg")

        try:
            columns, rows = _run(sql, backend)
        except Exception as exc:  # 语法错、字段错、超时
            problems.append(f"{tag}：标准 SQL 跑不起来 —— {exc}")
            continue

        if not rows:
            problems.append(f"{tag}：标准 SQL 返回 0 行")
        elif len(rows) == 1 and len(columns) == 1 and rows[0].get(columns[0]) is None:
            problems.append(f"{tag}：标准 SQL 单值聚合为 NULL")
        else:
            tie = _tie_at_cut(sql, columns, rows, backend)
            if tie:
                problems.append(f"{tag}：{tie}")

        # 两引擎结果集比对：只对给了 sql_pg 的题做
        if compare_engine is not None and item.get("sql_pg"):
            try:
                pg_columns, pg_rows = _run(item["sql_pg"], compare_engine)
            except Exception as exc:
                problems.append(f"{tag}：sql_pg 跑不起来 —— {exc}")
                continue
            if len(columns) != len(pg_columns):
                problems.append(
                    f"{tag}：sql 和 sql_pg 列数不同（{len(columns)} vs {len(pg_columns)}）"
                )
            elif _norm(columns, rows) != _norm(pg_columns, pg_rows):
                problems.append(f"{tag}：sql 和 sql_pg 结果集不一致")

    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", nargs="*", default=["questions.yml", "questions-holdout.yml"])
    ap.add_argument("--parity", action="store_true",
                    help="额外用 PostgreSQL 跑一遍 sql_pg 并比对（需要 TEST_POSTGRES_URL）")
    args = ap.parse_args()

    backend = get_backend()
    compare_engine = None
    if args.parity:
        from app.backends.postgres import PostgresBackend

        dsn = os.environ.get("TEST_POSTGRES_URL", "")
        if not dsn:
            print("--parity 需要在环境变量里给 TEST_POSTGRES_URL", file=sys.stderr)
            raise SystemExit(2)
        compare_engine = PostgresBackend(dsn)

    print(f"当前引擎：{dialect.display_name()}")
    total_q = 0
    total_problems = 0
    for name in args.questions:
        path = Path(name) if Path(name).is_absolute() else BASE / name
        items = yaml.safe_load(path.read_text(encoding="utf-8"))
        problems = check_file(path, backend, compare_engine)
        total_q += len(items)
        total_problems += len(problems)
        print(f"{path.name}: {len(items)} 条，问题 {len(problems)} 个")
        for p in problems:
            print(f"  [问题] {p}")

    print(f"\n合计 {total_q} 条，问题 {total_problems} 个")
    if total_problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
