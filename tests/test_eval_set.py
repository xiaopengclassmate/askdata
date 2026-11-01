"""评测集本身的完整性：不连库、不调模型，纯文本规则。

为什么值得单独测：评测集是「其他一切结论的依据」，它坏了不会有任何报错——
只会让准确率悄悄变得不可信。这几条是扩题时真正会踩的坑：

  · 题目漏了题型标签   → 按题型归因时掉进 uncategorized，等于没归因
  · 开发集和留出集撞题 → 留出集立刻失去「没调过参」的意义
  · 时间题只有 SQLite 版 → 换到 PostgreSQL 就没有标准答案可比

标准 SQL 能不能跑、跑出来是不是空，要用真库验，那条在 eval/check_questions.py
和 CI 里（要造数据，放单测里太重）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
FILES = ("questions.yml", "questions-holdout.yml")

CATEGORIES = {
    "time", "metric", "group", "topn", "ratio",
    "join", "negate", "snapshot", "customer", "compare",
}
SQLITE_ONLY = ("date('now'", "strftime(", "date(paid_at")


def _load(name: str) -> list[dict]:
    return yaml.safe_load((EVAL_DIR / name).read_text(encoding="utf-8"))


def _all_items() -> list[tuple[str, dict]]:
    return [(name, item) for name in FILES for item in _load(name)]


def test_set_is_big_enough():
    """扩题的目标是 200 条，少了说明文件被截断了"""
    total = sum(len(_load(name)) for name in FILES)
    assert total >= 200, f"评测集合计只有 {total} 条"


def test_every_question_is_well_formed():
    bad = []
    for name, item in _all_items():
        for field in ("q", "sql", "cat", "batch"):
            if not item.get(field):
                bad.append(f"{name}: {item.get('q', '<无题干>')} 缺 {field}")
        if item.get("cat") and item["cat"] not in CATEGORIES:
            bad.append(f"{name}: {item['q']} 的题型 {item['cat']} 不在定义里")
        if item.get("batch") not in ("v1", "v2"):
            bad.append(f"{name}: {item['q']} 的 batch 只能是 v1/v2")
    assert not bad, "\n".join(bad)


def test_no_duplicate_question_texts():
    """同一句话出现两次就是白占名额；跨文件重复还会让留出集失效"""
    seen: dict[str, str] = {}
    dup = []
    for name, item in _all_items():
        q = item["q"]
        if q in seen:
            dup.append(f"「{q}」同时出现在 {seen[q]} 和 {name}")
        seen[q] = name
    assert not dup, "\n".join(dup)


@pytest.mark.parametrize("name", FILES)
def test_new_questions_carry_both_dialects(name):
    """用了 SQLite 专有日期函数的题，必须同时给 sql_pg"""
    missing = [
        item["q"] for item in _load(name)
        if any(fn in item["sql"] for fn in SQLITE_ONLY) and not item.get("sql_pg")
    ]
    assert not missing, f"{name} 里这些题只有 SQLite 版标准答案：{missing}"


@pytest.mark.parametrize("name", FILES)
def test_question_texts_are_unique_in_file(name):
    texts = [item["q"] for item in _load(name)]
    assert len(texts) == len(set(texts))
