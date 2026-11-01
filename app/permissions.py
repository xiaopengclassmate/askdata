"""行级权限：把「谁能看到哪些门店」下沉到查询层。

核心前提：**模型不知道用户是谁**。所以权限绝不能靠提示词让它自己加
`WHERE store_id = 1`——模型会忘、换个问法就丢、被注入就绕。

也**不要**在拿到 SQL 之后去改写它：子查询、CTE、UNION、括号嵌套，手写字符串
注入条件必然有覆盖不到的写法，漏一个就是越权漏洞。

这里的做法是借 SQLite 的 TEMP VIEW：

    CREATE TEMP VIEW orders AS
      SELECT * FROM main.orders WHERE store_id IN (1, 3);

temp schema 的优先级高于 main，所以模型生成的 `FROM orders` 会被数据库自动
重定向到过滤后的视图——**SQL 一个字都不用改，模型全程无感**。过滤由 SQLite
自己完成，不存在「拼接漏了」这种可能；而且视图不可写，越权写还是会被拒。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# 演示用的身份。真实系统里这份映射来自登录态 / 组织架构，不该出现在代码里。
DEMO_ROLES: dict[str, dict] = {
    "hq": {"label": "总部（全部门店）", "stores": None},
    "south": {"label": "华南区经理（深圳、广州）", "stores": (1, 2)},
    "sz-nanshan": {"label": "深圳南山店店长", "stores": (1,)},
}


class ScopeError(ValueError):
    """身份不存在。"""


@dataclass(frozen=True)
class Scope:
    role: str
    label: str
    store_ids: tuple[int, ...] | None  # None = 不受限

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "label": self.label,
            "stores": list(self.store_ids) if self.store_ids else None,
        }


# 表名 → 过滤后的视图定义。占位符只填我们自己的整数 ID，不掺任何用户输入。
_SCOPED_VIEWS: dict[str, str] = {
    "store": "SELECT * FROM main.store WHERE id IN ({ids})",
    "orders": "SELECT * FROM main.orders WHERE store_id IN ({ids})",
    "order_item": (
        "SELECT * FROM main.order_item "
        "WHERE order_id IN (SELECT id FROM main.orders WHERE store_id IN ({ids}))"
    ),
    "refund": (
        "SELECT * FROM main.refund "
        "WHERE order_id IN (SELECT id FROM main.orders WHERE store_id IN ({ids}))"
    ),
    "inventory_snapshot": "SELECT * FROM main.inventory_snapshot WHERE store_id IN ({ids})",
}
# customer / product / category 是全局维表，任何身份都能全看，不建视图。

_ALL_SCOPE = Scope(role="hq", label="总部（全部门店）", store_ids=None)


def resolve(role: str | None) -> Scope:
    """把请求里的身份标识解析成 Scope。空 = 全量（公开 demo 的默认行为）。"""
    if not role:
        return _ALL_SCOPE
    spec = DEMO_ROLES.get(role)
    if spec is None:
        raise ScopeError(
            f"未知身份「{role}」，可选：{'、'.join(DEMO_ROLES)}"
        )
    return Scope(role=role, label=spec["label"], store_ids=spec["stores"])


def apply_scope(conn: sqlite3.Connection, scope: Scope | None) -> None:
    """在连接上建好受限视图。scope 为空或全量时什么都不做。

    必须在这一条连接上、执行查询之前建好——TEMP 对象是连接级的。
    """
    if scope is None or scope.store_ids is None:
        return
    ids = ",".join(str(int(i)) for i in scope.store_ids)
    for table, template in _SCOPED_VIEWS.items():
        conn.execute(f"CREATE TEMP VIEW {table} AS {template.format(ids=ids)}")
