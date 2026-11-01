"""多轮追问评测：把会话上文喂进去，看第二句还能不能答对。

只按最后一轮判定。前面几轮就算答错，也照样把它的 SQL 写进上文——
线上就是这样，用户看到上一轮结果不对，下一句追问的事实基础也就是错的。

用法：
    python eval/run_multiturn.py
    python eval/run_multiturn.py --limit 2
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

from app import dialect, session, text2sql  # noqa: E402
from eval.run_eval import _norm_rows, golden_rows  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    convs = yaml.safe_load((BASE / "multiturn.yml").read_text(encoding="utf-8"))
    if args.limit:
        convs = convs[: args.limit]

    passed, failed = 0, []
    started = time.monotonic()

    for i, conv in enumerate(convs, 1):
        gold = conv.get("sql_pg") or conv["sql"] if dialect.engine() == "postgres" else conv["sql"]
        expected = golden_rows(gold)
        store = session.SessionStore()
        sid = store.new_id()

        result, ok = None, False
        for question in conv["turns"]:
            history = session.render_history(store.history(sid))
            result = text2sql.answer(question, history=history)
            if not result.get("ok"):
                break
            store.append(sid, question, result["sql"], result.get("explanation", ""))
        else:
            ok = _norm_rows(result["columns"], result["rows"]) == expected

        if ok:
            passed += 1
            print(f"[{i}/{len(convs)}] PASS  {conv['name']}")
        else:
            if result.get("ok"):
                detail = (f"期望 {expected[:2]} "
                          f"实得 {_norm_rows(result['columns'], result['rows'])[:2]}")
            else:
                detail = result.get("clarify") or result.get("error", "生成失败")
            failed.append({"conversation": conv["name"], "turns": conv["turns"],
                           "detail": detail})
            print(f"[{i}/{len(convs)}] FAIL  {conv['name']}  —— {detail[:80]}")

    elapsed = time.monotonic() - started
    summary = {
        "total": len(convs),
        "passed": passed,
        "accuracy": round(passed / len(convs), 4),
        "elapsed_seconds": round(elapsed, 1),
        "failed": failed,
    }
    (BASE / "result-multiturn.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 60)
    print(f"多轮追问：{passed}/{len(convs)} = {summary['accuracy'] * 100:.1f}%  "
          f"耗时 {summary['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
