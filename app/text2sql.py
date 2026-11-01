"""Text2SQL 主链路：选表 → 生成 → 三重校验 → 执行 → 失败自修复 → 选图。

四道关卡，任何一道不过都不执行，报错回喂模型重试（最多 MAX_ATTEMPTS 次）：

    1. sql_guard  安全：只允许单条 SELECT、关键字黑名单、强制 LIMIT
    2. semantic   口径：表和 join 必须在语义层批准范围内
    3. dry_plan   语法：EXPLAIN 编译一遍，表名/字段名写错在这里就暴露
    4. 执行       运行时：超时、行数上限

这四层叠起来，模型最坏的情况是"查错但安全"，而不是"查错还写坏了库"。
"""

from __future__ import annotations

import time

from . import clarify, db, llm, semantic, sql_guard
from .config import settings
from .schema_catalog import TABLE_DOCS, render_schema

MAX_ATTEMPTS = 3
# 评测时关掉语义层段落做消融对比用；线上始终为 True
USE_SEMANTIC_PROMPT = True

SYSTEM_PROMPT = """你是一名资深数据分析师，负责把中文业务问题翻译成 SQLite 查询。

只输出一个 JSON 对象，不要任何解释文字，格式：
{"sql": "SELECT ...", "explanation": "一句话说明这条 SQL 在算什么", "chart": "line|bar|pie|table|metric"}

硬性要求：
- 只能写 SELECT 或 WITH 开头的单条查询语句
- 只能用下面列出的表和字段，不许编造
- 金额、口径必须严格按「指标定义」里的公式和 must_filter
- 不要输出注释，不要输出分号以外的多余语句

输出形状（列和行数要和问题问的对得上）：
- 只问「哪个 / 哪家 / 哪款 / 哪个月 / 谁……最……」且没有数量：只选那个对象本身一列，
  ORDER BY 指标 DESC LIMIT 1，不要把指标数值一起查出来
- 问题里带数量的（前 5 / Top 3 / 最高的 5 个 / 最低的 5 个 / 排名）：
  维度列 + 指标列都要返回，行数就是那个 N，按指标排序——「前 5」不是「只有第一名」
- 问「A 和 B 哪个多 / A 与 B 的对比」：对比的双方都要出行，不要只留赢的那个
- 问「各…… / 每个……」：维度列 + 指标列都返回，全部分组，不要 LIMIT 1
- 问「多少 / 几个 / 总计」：只返回一个聚合值
- 分组/分类的标签直接照抄字段原始取值：问题里的叫法往往只是口语化描述，
  值域以表结构里的 enums 为准（问「线上渠道」时，字段值可能就叫「线上」）
- 输出前自检：SQL 里的每个字符串常量，要么来自 enums 值域，要么来自问题里的筛选取值；
  自己新造的分组标签（例如把「线上」写成「线上渠道」）一律改回原值
- 除上面几条明确要求返回的指标列外，不要再附带额外列（中间计算、id、编号之类）"""

# 单独拎出来贴在问题后面：模型对「紧挨着问题」的指令遵循度明显更高，
# 而列形状错了（多一列少一列）在结果集比对里就是一条错答案。
SHAPE_HINT = """## 列形状（先按这条定好列，再写 SQL）
- 带数量的排名：前 5 / Top 3 / 最高的 3 个 / 最低的 5 个 / 排名 → 维度列 + 指标列，正好 N 行
- 只问「哪个最」且问题里没有数量 → 只返回那个对象一列，1 行
- 对比双方（A 和 B 哪个多 / A 与 B 对比）→ 双方各一行
- 各 / 每个 → 维度列 + 指标列，全部分组
- 追问里换了筛选条件（「那华南区呢」「只看 X」「换成 Y」）→ 这**不是**在问「哪个」，
  列和分组沿用上一轮，只改 WHERE
- 多少 / 几个 / 总计 → 只返回一个聚合值
- 分组标签照抄字段原始取值，不要自己拼「渠道」之类的后缀"""


def pick_tables(question: str, limit: int = 5) -> list[str]:
    """检索式 schema 裁剪：按问题里的词命中表/字段说明，只投喂相关的表。

    全量 schema 2000 多字不算大，但表数一多（30+ 表的生产库）就会噪声压过信号。
    这里先做个可解释的版本：命中词数打分，取前 N 张，订单主表永远带上。
    """
    q = question or ""
    scored: list[tuple[int, str]] = []
    for table, doc in TABLE_DOCS.items():
        haystack = " ".join([
            table, doc["name"], doc["desc"],
            *doc["columns"].keys(), *doc["columns"].values(), *doc.get("enums", []),
        ])
        score = sum(1 for token in _tokens(q) if token and token in haystack)
        scored.append((score, table))

    scored.sort(reverse=True)
    picked = [t for s, t in scored if s > 0][:limit]
    if not picked:                       # 完全没命中就退回核心两张表
        picked = ["orders", "order_item"]
    if "orders" not in picked:           # 订单主表几乎总要用到
        picked.append("orders")
    # 按原始顺序输出，模型读起来更自然
    return [t for t in TABLE_DOCS if t in picked]


def _tokens(text: str) -> list[str]:
    """极简分词：中文按 2 字滑窗 + 英文/数字整词。

    够用就不上 jieba——多一个依赖，收益在这个场景里看不出来。
    """
    tokens: list[str] = []
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text)
    for word in cleaned.split():
        tokens.append(word.lower())
    cjk = "".join(ch for ch in text if "一" <= ch <= "鿿")
    tokens.extend(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return [t for t in tokens if t.strip()]


def suggest_chart(columns: list[str], rows: list[dict]) -> str:
    """规则选图，不用模型：确定性、可测、出错也不会误导"""
    if not rows:
        return "table"
    n_cols = len(columns)
    numeric_cols = [c for c in columns if all(
        isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows
    )]
    if n_cols == 1 and len(rows) == 1:
        return "metric"
    if n_cols == 2 and len(numeric_cols) == 1:
        first_vals = [str(r.get(columns[0], "")) for r in rows]
        looks_like_date = all(len(v) >= 7 and v[:4].isdigit() for v in first_vals)
        return "line" if looks_like_date else "bar"
    if n_cols == 2 and len(rows) <= 8 and numeric_cols:
        return "pie"
    return "table"


def _build_user_prompt(question: str, tables: list[str], error_hint: str = "",
                       history: str = "") -> str:
    parts = [
        "## 数据表（只允许使用这些表）",
        render_schema(tables),
    ]
    if USE_SEMANTIC_PROMPT:
        parts += [semantic.render_joins(), semantic.render_prompt_block()]
    if history:
        parts.append(
            "## 上文（同一会话里刚才问过的，只用来理解「那上个月呢」这类追问；"
            "和本次问题冲突时以本次为准）\n" + history
            + "\n追问规则：本次问题里省略的条件（时间范围、指标、维度）从上文继承；"
              "只替换本次明确说出来的部分（比如「换成华南区」只改筛选条件，"
              "时间和指标沿用上一条 SQL）；上一轮选了哪几列、按什么分组，这一轮也照样选，"
              "除非本次问题明确要求换列或换分组。"
              "把上文那条 SQL 当作起点改写，不要抛开它重新写一条完全不同的查询。"
        )
    parts.append(f"## 问题\n{question}")
    parts.append(SHAPE_HINT)
    if error_hint:
        parts.append(f"## 上一次生成的 SQL 有问题，请修正后重新输出 JSON\n{error_hint}")
    return "\n\n".join(parts)


def answer(question: str, history: str = "") -> dict:
    """跑完整条链路。返回 dict，包含 sql / 数据 / 图类型 / 重试次数 / 各阶段耗时。"""
    started = time.monotonic()

    # 问题本身没说清时不猜，先反问（确定性规则，不消耗模型调用）
    hint = clarify.need_clarify(question, has_history=bool(history.strip()))
    if hint:
        return {
            "ok": False,
            "need_clarify": True,
            "clarify_kind": hint.kind,
            "clarify": hint.message,
            "clarify_options": hint.options,
            "question": question,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    tables = pick_tables(question)
    hints: list[str] = []
    last_error = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = llm.complete(
            SYSTEM_PROMPT, _build_user_prompt(question, tables, last_error, history)
        )
        payload = llm.parse_sql_json(raw)
        sql = (payload.get("sql") or "").strip()

        guard = sql_guard.check(sql)
        if not guard:
            last_error = f"安全网关拒绝：{guard.reason}"
            hints.append(f"第 {attempt} 次 · {last_error}")
            continue

        problems = semantic.check(guard.sql)
        if problems:
            last_error = "语义层校验失败：" + "；".join(problems)
            hints.append(f"第 {attempt} 次 · {last_error}")
            continue

        plan_error = db.dry_plan(guard.sql)
        if plan_error:
            last_error = f"SQL 编译失败：{plan_error}"
            hints.append(f"第 {attempt} 次 · {last_error}")
            continue

        try:
            result = db.execute_query(guard.sql)
        except db.QueryTimeout as exc:
            last_error = f"查询超时：{exc}"
            hints.append(f"第 {attempt} 次 · {last_error}")
            continue
        except Exception as exc:
            last_error = f"执行报错：{exc}"
            hints.append(f"第 {attempt} 次 · {last_error}")
            continue

        return {
            "ok": True,
            "question": question,
            "sql": guard.sql,
            "explanation": payload.get("explanation", ""),
            "columns": result.columns,
            "rows": result.rows,
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "chart": payload.get("chart") or suggest_chart(result.columns, result.rows),
            "tables_used": tables,
            "attempts": attempt,
            "retry_log": hints,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "query_ms": result.elapsed_ms,
        }

    return {
        "ok": False,
        "question": question,
        "attempts": MAX_ATTEMPTS,
        "retry_log": hints,
        "error": last_error or "模型未能生成可执行的 SQL",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def health() -> dict:
    return {
        "llm_enabled": settings.llm_enabled,
        "max_attempts": MAX_ATTEMPTS,
        "tables": len(TABLE_DOCS),
    }
