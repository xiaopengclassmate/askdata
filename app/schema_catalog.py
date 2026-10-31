"""业务库的中文语义目录 —— 喂给模型的「schema 知识」。

为什么要单独一份而不是靠数据库注释：
  1. SQLite 会丢掉 COMMENT，MySQL 的注释也没法描述口径；
  2. 模型需要的不是字段类型，而是**业务口径**（金额算哪个字段、取消单要不要排除）；
  3. 以后换库（SQLite → PostgreSQL）业务代码一行不用改。

`TABLE_DOCS` 是全量目录，`render_schema()` 负责把它渲染成文本。
后续 `schema_retriever` 会按问题挑相关表，只投喂子集，减少噪声。
"""

from __future__ import annotations

# 表 -> (中文名, 用途说明, {字段: 说明}, [枚举值说明])
TABLE_DOCS: dict[str, dict] = {
    "store": {
        "name": "门店",
        "desc": "线下门店信息，每个门店属于一个区域",
        "columns": {
            "id": "门店 ID",
            "name": "门店名称",
            "city": "所在城市",
            "region": "所属区域（华东 / 华南 / 华北 / 西南）",
            "opened_at": "开业日期",
        },
        "enums": ["region ∈ {华东, 华南, 华北, 西南}"],
    },
    "category": {
        "name": "商品分类",
        "desc": "商品二级分类",
        "columns": {
            "id": "分类 ID",
            "name": "分类名称（手机数码 / 家用电器 / 食品生鲜 / 服饰鞋包 / 美妆个护）",
            "parent_name": "父级分类名，当前都为空",
        },
        "enums": [],
    },
    "product": {
        "name": "商品",
        "desc": "在售和停售商品的基础信息",
        "columns": {
            "id": "商品 ID",
            "name": "商品名称",
            "category_id": "所属分类 ID，关联 category.id",
            "price": "标准售价（不是成交价，成交价看 order_item.unit_price）",
            "cost": "成本价，毛利率用 (成交价 - cost) / 成交价",
            "status": "在售 / 停售",
        },
        "enums": ["status ∈ {在售, 停售}"],
    },
    "customer": {
        "name": "客户",
        "desc": "注册客户，一个客户可下多笔订单",
        "columns": {
            "id": "客户 ID",
            "name": "客户姓名",
            "gender": "性别（男 / 女）",
            "birthday": "生日，算年龄用 date('now') - birthday",
            "city": "客户所在城市（与门店城市可能不同）",
            "level": "会员等级：普通 / 银卡 / 金卡",
            "registered_at": "注册日期",
        },
        "enums": ["gender ∈ {男, 女}", "level ∈ {普通, 银卡, 金卡}"],
    },
    "orders": {
        "name": "订单主表",
        "desc": "一笔订单一行。金额口径：实收 = total_amount - discount_amount；"
                "统计营收时**必须排除 status = '已取消'**，否则会把没成交的单算进去",
        "columns": {
            "id": "订单 ID",
            "order_no": "订单号",
            "customer_id": "下单客户 ID，关联 customer.id",
            "store_id": "成交门店 ID，关联 store.id",
            "status": "已支付 / 已发货 / 已完成 / 已取消",
            "channel": "下单渠道：门店 / 小程序 / 天猫",
            "total_amount": "应付金额（优惠前）",
            "discount_amount": "优惠金额",
            "paid_at": "支付时间，字符串格式 'YYYY-MM-DD HH:MM:SS'，取消单为 NULL；"
                       "按时间筛选用它，不要用 id",
            "created_at": "下单时间，格式同上",
        },
        "enums": ["status ∈ {已支付, 已发货, 已完成, 已取消}", "channel ∈ {门店, 小程序, 天猫}"],
    },
    "order_item": {
        "name": "订单明细",
        "desc": "一笔订单里每个商品一行。按商品统计销量 / 销售额要关联这张表",
        "columns": {
            "id": "明细 ID",
            "order_id": "所属订单 ID，关联 orders.id",
            "product_id": "商品 ID，关联 product.id",
            "quantity": "购买数量",
            "unit_price": "成交单价（已含活动价，不是 product.price）",
            "amount": "该明细金额 = unit_price * quantity",
        },
        "enums": [],
    },
    "refund": {
        "name": "退款",
        "desc": "退款记录。退款率 = 退款笔数 / 已完成订单数；"
                "退款金额口径只统计 status = '已退款'",
        "columns": {
            "id": "退款单 ID",
            "order_id": "关联订单 ID",
            "order_item_id": "关联明细 ID，可能为空（整单退款）",
            "reason": "退款原因：质量问题 / 七天无理由 / 发错货 / 其他",
            "amount": "退款金额",
            "status": "处理中 / 已退款 / 已驳回",
            "created_at": "申请时间",
        },
        "enums": ["reason ∈ {质量问题, 七天无理由, 发错货, 其他}",
                  "status ∈ {处理中, 已退款, 已驳回}"],
    },
    "inventory_snapshot": {
        "name": "库存快照",
        "desc": "按门店 + 商品 + 日期的库存快照，只保留最近 30 天、仅深圳南山店和广州天河店",
        "columns": {
            "store_id": "门店 ID",
            "product_id": "商品 ID",
            "snapshot_date": "快照日期 'YYYY-MM-DD'",
            "stock": "当日结存库存",
            "sold": "当日销量",
        },
        "enums": [],
    },
}


# 少样本：挑的是最容易写错的几种问法（口径、时间、环比、join）
FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "q": "上个月总营收是多少",
        "sql": "SELECT ROUND(SUM(total_amount - discount_amount), 2) AS revenue "
               "FROM orders WHERE status <> '已取消' "
               "AND paid_at >= date('now', 'start of month', '-1 month') "
               "AND paid_at < date('now', 'start of month')",
    },
    {
        "q": "最近 30 天各门店的订单量和实收金额，按实收降序",
        "sql": "SELECT s.name AS store, COUNT(o.id) AS order_cnt, "
               "ROUND(SUM(o.total_amount - o.discount_amount), 2) AS revenue "
               "FROM orders o JOIN store s ON s.id = o.store_id "
               "WHERE o.status <> '已取消' AND o.paid_at >= date('now', '-30 days') "
               "GROUP BY s.name ORDER BY revenue DESC",
    },
    {
        "q": "销量前 5 的商品",
        "sql": "SELECT p.name AS product, SUM(i.quantity) AS qty "
               "FROM order_item i JOIN product p ON p.id = i.product_id "
               "JOIN orders o ON o.id = i.order_id "
               "WHERE o.status <> '已取消' GROUP BY p.name "
               "ORDER BY qty DESC LIMIT 5",
    },
]


def render_schema(tables: list[str] | None = None) -> str:
    """把目录渲染成给模型看的文本；tables 为 None 时输出全量"""
    picked = tables or list(TABLE_DOCS)
    out: list[str] = []
    for table in picked:
        doc = TABLE_DOCS.get(table)
        if not doc:
            continue
        out.append(f"### {table}（{doc['name']}）")
        out.append(doc["desc"])
        for col, desc in doc["columns"].items():
            out.append(f"  - {col}: {desc}")
        for enum in doc["enums"]:
            out.append(f"  - 取值: {enum}")
        out.append("")
    return "\n".join(out)


def render_few_shot() -> str:
    return "\n\n".join(f"问题：{e['q']}\nSQL：{e['sql']}" for e in FEW_SHOT_EXAMPLES)
