"""SQL 安全网关 —— 模型生成的东西不能直接丢给数据库执行。

大模型生成的 SQL 是**外部输入**，和用户表单没区别。这里做四件事：
  1. 只允许单条 SELECT / WITH 语句（禁 DDL、DML、ATTACH、PRAGMA…）
  2. 剥掉注释、检查有没有分号拼接的多语句
  3. 强制 LIMIT，防止 SELECT * 把百万行打回内存
  4. 交给 db 层用只读连接 + 超时执行（见 db.execute_query）

这一层是纯函数，好写单测；线上即使模型被注入，最坏也只能读到本来就该看到的数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 出现这些关键字一律拒绝（按词边界匹配，避免误伤 status 这类字段名）
FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "grant", "revoke", "attach", "detach", "pragma", "vacuum",
    "reindex", "load_extension",
)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


@dataclass
class GuardResult:
    ok: bool
    sql: str
    reason: str = ""

    def __bool__(self) -> bool:  # 方便 `if result:` 判断
        return self.ok


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def normalize(sql: str) -> str:
    """去注释、压空白，方便做结构判断（不改变语义之外的东西）"""
    return re.sub(r"\s+", " ", _strip_comments(sql)).strip()


def check(sql: str, default_limit: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT) -> GuardResult:
    raw = (sql or "").strip().strip("`").strip()
    if not raw:
        return GuardResult(False, "", "SQL 为空")

    body = normalize(raw)

    # 1) 只允许单条语句：去掉结尾分号后不能再有分号
    trimmed = body.rstrip(";").strip()
    if ";" in trimmed:
        return GuardResult(False, "", "只允许执行单条语句")

    # 2) 必须以 SELECT / WITH 开头
    if not re.match(r"(?is)^(select|with)\b", trimmed):
        return GuardResult(False, "", "只允许查询语句（SELECT / WITH）")

    # 3) 黑名单关键字（按词边界）
    lowered = trimmed.lower()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            return GuardResult(False, "", f"语句包含不允许的关键字：{kw}")

    # 4) 强制 LIMIT
    has_limit = re.search(r"(?is)\blimit\s+\d+", trimmed) is not None
    if has_limit:
        m = re.search(r"(?is)\blimit\s+(\d+)", trimmed)
        if m and int(m.group(1)) > max_limit:
            trimmed = trimmed[: m.start(1)] + str(max_limit) + trimmed[m.end(1):]
    else:
        trimmed = f"{trimmed} LIMIT {default_limit}"

    return GuardResult(True, trimmed)
