"""结果自检：SQL 跑通了，不等于答对了。

现有的自修复只管「跑不起来」——语法错、字段名错、被网关拦。但更常见的一类
错法是：SQL 完全合法、执行成功、**返回一个空结果**。典型两种：

  · 聚合被 WHERE 全过滤掉 → SUM/AVG 返回 NULL
  · 筛选值写成了库里不存在的叫法 → 0 行（模型很爱把「线上」扩写成「线上渠道」）

这两种没有任何异常可以回喂，模型自己也不知道错了，跑出来的东西还长得像答案。
所以补一层对**结果**的确定性检查，判成可疑就把原因当错误回喂，让它带着这条线索重写。

设计上刻意保守：只认「完全无法解释的空结果」。0、负数、并列、超预期的大数
一律不管——那些要么合法，要么需要业务知识才能判断，硬判会误伤正确答案。
"""

from __future__ import annotations

import re

_STRING_LITERAL = re.compile(r"'([^']*)'")
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _label_literals(sql: str) -> list[str]:
    """挑出可能表示「标签取值」的字符串常量。

    日期和纯数字不算——`paid_at >= '2026-10-01'` 查不到东西是时间范围问题，
    不是「这个取值在库里不存在」，回喂错线索会让模型乱改。
    """
    labels = []
    for match in _STRING_LITERAL.finditer(sql):
        value = match.group(1).strip()
        if value and not value.isdigit() and not _DATE_LIKE.match(value):
            labels.append(value)
    return labels


def check_result(columns: list[str], rows: list[dict], sql: str) -> str:
    """返回可疑原因（会原样回喂给模型）；判断没问题时返回空字符串。"""
    labels = _label_literals(sql)
    label_hint = (
        f"；条件里的取值「{labels[0]}」也可能是库里不存在的叫法，"
        "请对照表结构的 enums 用原始取值，不要自己拼标签"
        if labels
        else ""
    )

    if not rows:
        if labels:
            return (
                f"查询返回 0 行：条件里的取值「{labels[0]}」在库里可能不存在或叫法不同，"
                "请对照表结构的 enums 用原始取值重写 WHERE，不要自己拼标签"
            )
        return "查询返回 0 行：join 条件或时间范围可能写错了，请检查后重写"

    # 单值聚合为空：SUM/AVG 在没有匹配行时返回 NULL，几乎都是过滤条件写错
    if len(rows) == 1 and len(columns) == 1 and rows[0].get(columns[0]) is None:
        return (
            "聚合结果为 NULL：WHERE 可能把数据全过滤掉了（状态过滤写反或者缺了必要条件）"
            f"{label_hint}。请检查过滤条件后重写"
        )

    return ""
