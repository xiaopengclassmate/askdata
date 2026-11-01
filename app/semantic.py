"""语义层：加载指标定义，渲染给模型，并在生成后校验 join 是否被批准。

和 sql_guard 的分工要分清：
  · sql_guard 管「这条语句安不安全」（会不会写库、会不会拖库）
  · semantic  管「这条语句对不对」（口径对不对、表和表之间该不该连）
两者都不通过就不执行，报错信息会回喂模型重试。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from . import dialect
from .schema_catalog import TABLE_DOCS

SEMANTIC_FILE = Path(__file__).resolve().parent.parent / "semantic_layer" / "retail.yml"

_TABLE_RE = re.compile(r"(?is)\b(?:from|join)\s+([a-z_][a-z0-9_]*)")
# FROM/JOIN 后面可能跟别名：FROM orders o / JOIN store s
_FROM_JOIN_RE = re.compile(
    r"(?is)\b(?:from|join)\s+([a-z_][a-z0-9_]*)(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?"
)
# 每个 JOIN xxx ON <条件> 的条件部分
_JOIN_ON_RE = re.compile(
    r"(?is)\bjoin\s+([a-z_][a-z0-9_]*)(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?"
    r"\s+on\s+(.*?)(?=\bjoin\b|\bwhere\b|\bgroup\b|\border\b|\blimit\b|$)"
)
_ALIAS_STOPWORDS = {"on", "where", "group", "order", "limit", "join", "left",
                    "inner", "outer", "as", "and", "or", "using"}


@lru_cache(maxsize=1)
def load() -> dict:
    with SEMANTIC_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _conventions(model: dict) -> list[str]:
    """展开口径列表：@time 换成当前引擎的时间写法，其余原样。

    之所以用占位符而不是整段复制一份，是因为两条规则之间的顺序会影响模型
    的遵循度，复制一份迟早会改漏一处。
    """
    engine = dialect.engine()
    time_rules = (model.get("conventions_time") or {}).get(engine, [])
    out: list[str] = []
    for line in model.get("conventions", []):
        if str(line).strip().startswith("@time"):
            out.extend(time_rules)
        else:
            out.append(line)
    return out


def render_prompt_block() -> str:
    """渲染成 prompt 里的一段：指标 + 口径 + 示例"""
    model = load()
    out = ["## 可用指标（只能从这些指标里取公式，不要自己发明）"]
    for m in model.get("metrics", []):
        line = f"- {m['name']}: {m['formula']}"
        if m.get("must_filter"):
            line += f"  【必须】WHERE {m['must_filter']}"
        if m.get("note"):
            line += f"  （{m['note']}）"
        out.append(line)

    out.append("\n## 口径规则（违反即为错误）")
    out.extend(f"- {c}" for c in _conventions(model))

    examples = model.get("examples", [])
    if dialect.engine() == "postgres":
        examples = model.get("examples_postgres") or examples
    out.append("\n## 示例")
    for e in examples:
        out.append(f"问题：{e['q']}\nSQL：{' '.join(str(e['sql']).split())}")
    return "\n".join(out)


def metric_names_hit(question: str) -> list[str]:
    """问题里出现了哪些指标名，用于挑少样本和给模型提示"""
    q = question or ""
    return [m["name"] for m in load().get("metrics", []) if m["name"] in q]


def _approved_pairs() -> set[frozenset[str]]:
    return {frozenset((a, b)) for a, b, _ in load().get("approved_joins", [])}


def tables_in(sql: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).lower() for m in _TABLE_RE.finditer(sql)))


def _alias_map(sql: str) -> dict[str, str]:
    """别名 -> 真实表名。把 o 映射到 orders，ON 条件里才好还原"""
    mapping: dict[str, str] = {}
    for m in _FROM_JOIN_RE.finditer(sql):
        table = m.group(1).lower()
        mapping[table] = table
        alias = (m.group(2) or "").lower()
        if alias and alias not in _ALIAS_STOPWORDS:
            mapping[alias] = table
    return mapping


def join_edges(sql: str) -> list[frozenset[str]]:
    """抽取 SQL 里真实的连接边：每个 JOIN 的 ON 条件连接了哪两张表。

    只校验「实际被连起来」的边，而不是所有表的笛卡尔组合——
    三表查询里 product 和 orders 不需要直连，各自连着 order_item 就够了。
    """
    aliases = _alias_map(sql)
    edges: list[frozenset[str]] = []
    for m in _JOIN_ON_RE.finditer(sql):
        joined = m.group(1).lower()
        on_clause = m.group(3)
        touched = {
            aliases.get(ident, ident)
            for ident in re.findall(r"[a-z_][a-z0-9_]*", on_clause.lower())
            if aliases.get(ident, ident) in TABLE_DOCS
        }
        for other in touched:
            if other != joined:
                edges.append(frozenset((joined, other)))
    return edges


def check(sql: str) -> list[str]:
    """返回语义层层面发现的问题；空列表表示通过"""
    problems: list[str] = []
    tables = tables_in(sql)

    unknown = [t for t in tables if t not in TABLE_DOCS]
    if unknown:
        problems.append(f"引用了不存在的表：{', '.join(unknown)}")
        return problems

    approved = _approved_pairs()
    seen: set[frozenset[str]] = set()
    for edge in join_edges(sql):
        if edge in seen:
            continue
        seen.add(edge)
        if edge not in approved:
            left, right = sorted(edge)
            problems.append(
                f"表 {left} 与 {right} 之间没有批准的关联路径，请换一种写法或只用一张表"
            )
    return problems


def render_joins() -> str:
    """给 prompt 用的 join 白名单"""
    lines = ["## 允许的表关联"]
    for a, b, cond in load().get("approved_joins", []):
        lines.append(f"- {a} JOIN {b} ON {cond}")
    return "\n".join(lines)
