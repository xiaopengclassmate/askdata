"""评测脚本：跑完整 60 条问题，用「结果集比对」判对错。

用法：
    python eval/run_eval.py                # 完整链路（语义层 + 自修复，最多 3 次）
    python eval/run_eval.py --mode baseline  # 基线：不校验语义、不重试（模拟裸 prompt）
    python eval/run_eval.py --limit 10     # 先跑 10 条看看
    DATABASE_URL=postgresql://... python eval/run_eval.py   # 在 PostgreSQL 上跑

两种模式跑同一个评测集，才有「X% → Y%」这个数字可讲。
标准答案分方言：带 sql_pg 的题，在 PostgreSQL 上会用那一份。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

from app import db, dialect, semantic, sql_guard, text2sql  # noqa: E402


def _norm_rows(columns: list[str], rows: list[dict]) -> list[tuple]:
    """把结果归一化成可比较的形式：数值保留 2 位，行内按列顺序取值"""
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


def golden_rows(sql: str) -> list[tuple]:
    guard = sql_guard.check(sql)
    result = db.execute_query(guard.sql)
    return _norm_rows(result.columns, result.rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "baseline"], default="full")
    ap.add_argument("--limit", type=int, default=0)
    # 留出集用 --questions questions-holdout.yml --out result-holdout.json，
    # 别覆盖开发集的 result-full.json，两组数字要能分开讲。
    ap.add_argument("--questions", default="questions.yml")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    questions = yaml.safe_load((BASE / args.questions).read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[: args.limit]

    if args.mode == "baseline":
        # 裸 prompt：无语义层段落、不做语义校验、不给重试机会（安全网关保留，否则会写坏库）
        text2sql.MAX_ATTEMPTS = 1
        text2sql.USE_SEMANTIC_PROMPT = False
        semantic.check = lambda sql: []  # type: ignore[assignment]

    passed, failed = 0, []
    started = time.monotonic()

    for i, item in enumerate(questions, 1):
        question = item["q"]
        # 标准答案也分方言：PostgreSQL 上要用 date_trunc / interval 那一份
        if dialect.engine() == "postgres":
            gold_sql = item.get("sql_pg") or item["sql"]
        else:
            gold_sql = item["sql"]
        expected = golden_rows(gold_sql)

        result = text2sql.answer(question)
        ok = False
        detail = ""
        if not result.get("ok"):
            detail = result.get("error", "")
        else:
            got = _norm_rows(result["columns"], result["rows"])
            if got == expected:
                ok = True
            else:
                detail = f"结果不符：期望 {expected[:2]} 实得 {got[:2]}"

        if ok:
            passed += 1
            mark = "PASS"
        else:
            failed.append({"q": question, "detail": detail,
                           "sql": result.get("sql", "")})
            mark = "FAIL"
        print(f"[{i:>2}/{len(questions)}] {mark}  {question}"
              + (f"  —— {detail[:80]}" if detail else ""))

    elapsed = time.monotonic() - started
    n = len(questions)
    summary = {
        "mode": args.mode,
        "total": n,
        "passed": passed,
        "accuracy": round(passed / n, 4),
        "elapsed_seconds": round(elapsed, 1),
        "avg_seconds_per_question": round(elapsed / n, 2),
        "failed": failed,
    }

    out = BASE / (args.out or f"result-{args.mode}.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"模式 {args.mode}：{passed}/{n} = {summary['accuracy'] * 100:.1f}%  "
          f"耗时 {summary['elapsed_seconds']}s（平均 {summary['avg_seconds_per_question']}s/题）")
    print(f"明细写到 {out}")


if __name__ == "__main__":
    main()
