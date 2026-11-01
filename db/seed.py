"""生成演示业务库：连锁零售电商，约 6000 笔订单 / 2 年数据。

用法：  python db/seed.py                       # 默认重建 db/askdata.db（SQLite）
        python db/seed.py --orders 20000        # 换订单量
        python db/seed.py --url postgresql://postgres@localhost:5432/askdata
                                                # 灌进 PostgreSQL（同一份数据、同一个随机种子）

设计要点：
  · 固定随机种子，同一份代码每次跑出来的数据一样，评测集才能复现
  · 收入带季节性和门店差异，这样「上月环比」「华南区占比」这类问题才有非平凡答案
  · 故意留一点脏数据（少量未支付订单、部分退款），用来验证模型生成的 where 条件
  · 两种引擎生成的数据必须逐行一致，否则「换库」这件事没法验证
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "askdata.db"
SCHEMA_PATH = BASE / "schema.sql"
SCHEMA_PG_PATH = BASE / "schema_pg.sql"

STORES = [
    ("深圳南山店", "深圳", "华南"),
    ("广州天河店", "广州", "华南"),
    ("上海徐汇店", "上海", "华东"),
    ("杭州西湖店", "杭州", "华东"),
    ("北京朝阳店", "北京", "华北"),
    ("成都锦江店", "成都", "西南"),
]

CATEGORIES = [
    ("手机数码", None),
    ("家用电器", None),
    ("食品生鲜", None),
    ("服饰鞋包", None),
    ("美妆个护", None),
]

# (商品名, 分类下标, 售价, 成本率)
PRODUCTS = [
    ("智能手机 X1", 0, 2999, 0.68), ("智能手机 X1 Pro", 0, 4599, 0.66),
    ("无线耳机 A2", 0, 499, 0.45), ("智能手表 W3", 0, 1299, 0.52),
    ("平板 P8", 0, 2499, 0.70), ("移动电源 20000mAh", 0, 129, 0.40),
    ("滚筒洗衣机 10kg", 1, 2899, 0.62), ("变频空调 1.5匹", 1, 3199, 0.60),
    ("空气炸锅 5L", 1, 399, 0.42), ("扫地机器人 R5", 1, 1899, 0.55),
    ("电热水壶", 1, 159, 0.38), ("冷藏冰箱 452L", 1, 4299, 0.64),
    ("有机蓝莓 500g", 2, 69, 0.55), ("进口牛排 1kg", 2, 189, 0.60),
    ("有机蔬菜礼盒", 2, 129, 0.50), ("云南咖啡豆 500g", 2, 99, 0.45),
    ("坚果大礼包", 2, 159, 0.48), ("鲜牛奶 1L", 2, 19, 0.62),
    ("男士休闲外套", 3, 459, 0.35), ("女士连衣裙", 3, 399, 0.33),
    ("运动跑鞋", 3, 599, 0.40), ("牛仔裤", 3, 299, 0.36),
    ("双肩背包", 3, 259, 0.34), ("羊毛围巾", 3, 189, 0.32),
    ("保湿面霜 50ml", 4, 329, 0.30), ("氨基酸洁面", 4, 129, 0.28),
    ("防晒喷雾", 4, 159, 0.29), ("香水 50ml", 4, 699, 0.27),
    ("牙膏三支装", 4, 79, 0.35), ("洗发水 750ml", 4, 89, 0.32),
]

REFUND_REASONS = ["质量问题", "七天无理由", "发错货", "其他"]
CHANNELS = ["门店", "小程序", "天猫"]
CHANNEL_WEIGHTS = [0.5, 0.3, 0.2]
CUSTOMER_CITIES = ["深圳", "广州", "上海", "杭州", "北京", "成都", "东莞", "苏州"]


def month_factor(month: int) -> float:
    """月度季节性：11/12 大促、2 月淡季，让「环比」类问题有真实波动"""
    return {1: 0.9, 2: 0.72, 3: 0.95, 4: 0.92, 5: 1.0, 6: 1.15,
            7: 0.95, 8: 0.93, 9: 1.0, 10: 1.05, 11: 1.45, 12: 1.35}[month]


class _Writer:
    """两种引擎的写入差异只有三处：连接、占位符、建表脚本。收在这里，灌数逻辑只写一份。"""

    def __init__(self, url: str | None = None) -> None:
        self.is_pg = bool(url)
        if self.is_pg:
            import psycopg

            self.conn = psycopg.connect(url)
            self.placeholder = "%s"
            schema_sql = SCHEMA_PG_PATH.read_text(encoding="utf-8")
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.placeholder = "?"
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

        # 逐条执行：建表脚本里有 DROP/CREATE/INDEX，一次执行多条在各驱动上行为不一致
        for statement in schema_sql.split(";"):
            body = "\n".join(
                line for line in statement.splitlines() if not line.strip().startswith("--")
            ).strip()
            if body:
                self.conn.execute(body)

    def many(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        sql = sql.replace("?", self.placeholder)
        if self.is_pg:
            with self.conn.cursor() as cur:
                cur.executemany(sql, rows)
        else:
            self.conn.executemany(sql, rows)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def build(orders_count: int, url: str | None = None) -> None:
    random.seed(20260101)

    conn = _Writer(url)

    conn.many(
        "INSERT INTO store(id, name, city, region, opened_at) VALUES (?,?,?,?,?)",
        [(i + 1, n, c, r, "2019-06-01") for i, (n, c, r) in enumerate(STORES)],
    )
    conn.many(
        "INSERT INTO category(id, name, parent_name) VALUES (?,?,?)",
        [(i + 1, n, p) for i, (n, p) in enumerate(CATEGORIES)],
    )
    conn.many(
        "INSERT INTO product(id, name, category_id, price, cost, status) "
        "VALUES (?,?,?,?,?,?)",
        [
            (i + 1, name, cat + 1, price, round(price * rate, 2),
             "停售" if i % 13 == 12 else "在售")
            for i, (name, cat, price, rate) in enumerate(PRODUCTS)
        ],
    )

    # ---- 客户 ----
    customers = []
    for i in range(1, 801):
        reg = date(2023, 1, 1) + timedelta(days=random.randint(0, 900))
        customers.append((
            i, f"客户{i:03d}", random.choice(["男", "女"]),
            date(random.randint(1970, 2005), random.randint(1, 12), random.randint(1, 28)).isoformat(),
            random.choice(CUSTOMER_CITIES),
            random.choices(["普通", "银卡", "金卡"], [0.7, 0.22, 0.08])[0],
            reg.isoformat(),
        ))
    conn.many(
        "INSERT INTO customer(id, name, gender, birthday, city, level, registered_at) "
        "VALUES (?,?,?,?,?,?,?)", customers,
    )

    # ---- 订单 ----
    today = date.today()
    start = today - timedelta(days=730)
    weighted_days = []
    d = start
    while d <= today:
        # 周末单量更高；乘上月度季节性
        f = month_factor(d.month) * (1.25 if d.weekday() >= 5 else 1.0)
        weighted_days.append((d, f))
        d += timedelta(days=1)

    days = [x[0] for x in weighted_days]
    weights = [x[1] for x in weighted_days]

    order_rows, item_rows, refund_rows = [], [], []
    order_id = 0
    for _ in range(orders_count):
        order_id += 1
        day = random.choices(days, weights)[0]
        status = random.choices(
            ["已完成", "已发货", "已支付", "已取消"], [0.82, 0.08, 0.06, 0.04]
        )[0]
        created = f"{day.isoformat()} {random.randint(8, 22):02d}:{random.randint(0, 59):02d}:00"
        paid_at = None if status == "已取消" else created
        store_id = random.randint(1, len(STORES))
        customer_id = random.randint(1, 800)
        channel = random.choices(CHANNELS, CHANNEL_WEIGHTS)[0]

        n_items = random.choices([1, 2, 3, 4], [0.55, 0.27, 0.13, 0.05])[0]
        picked = random.sample(range(1, len(PRODUCTS) + 1), n_items)
        total = 0.0
        this_items = []
        for pid in picked:
            price = PRODUCTS[pid - 1][2]
            qty = random.choices([1, 2, 3], [0.8, 0.15, 0.05])[0]
            amount = round(price * qty, 2)
            total += amount
            this_items.append((pid, qty, price, amount))
        discount = round(total * random.choice([0, 0, 0, 0.05, 0.1, 0.15]), 2)

        order_rows.append((
            order_id, f"SO{day.strftime('%Y%m%d')}{order_id:06d}", customer_id,
            store_id, status, channel, round(total, 2), discount, paid_at, created,
        ))
        for k, (pid, qty, price, amount) in enumerate(this_items):
            item_id = len(item_rows) + 1
            item_rows.append((item_id, order_id, pid, qty, price, amount))
            # 约 6% 的明细产生退款
            if status == "已完成" and random.random() < 0.06:
                refund_rows.append((
                    len(refund_rows) + 1, order_id, item_id,
                    random.choice(REFUND_REASONS),
                    round(amount * random.choice([1.0, 1.0, 0.5]), 2),
                    random.choices(["已退款", "处理中", "已驳回"], [0.85, 0.1, 0.05])[0],
                    created,
                ))

    conn.many(
        "INSERT INTO orders(id, order_no, customer_id, store_id, status, channel, "
        "total_amount, discount_amount, paid_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        order_rows,
    )
    conn.many(
        "INSERT INTO order_item(id, order_id, product_id, quantity, unit_price, amount) "
        "VALUES (?,?,?,?,?,?)", item_rows,
    )
    conn.many(
        "INSERT INTO refund(id, order_id, order_item_id, reason, amount, status, created_at) "
        "VALUES (?,?,?,?,?,?,?)", refund_rows,
    )

    # ---- 最近 30 天的库存快照（只给两家店，够跑「畅销/滞销」类问题）----
    snaps = []
    for offset in range(30):
        snap_day = (today - timedelta(days=offset)).isoformat()
        for store_id in (1, 2):
            for pid in range(1, len(PRODUCTS) + 1):
                snaps.append((store_id, pid, snap_day,
                              random.randint(0, 200), random.randint(0, 30)))
    conn.many(
        "INSERT INTO inventory_snapshot(store_id, product_id, snapshot_date, stock, sold) "
        "VALUES (?,?,?,?,?)", snaps,
    )

    conn.close()


def _report(url: str | None) -> None:
    if url:
        import psycopg

        conn = psycopg.connect(url)
        label = url
    else:
        conn = sqlite3.connect(DB_PATH)
        label = str(DB_PATH)

    print(f"数据库：{label}")
    for table in ("store", "category", "product", "customer", "orders",
                  "order_item", "refund", "inventory_snapshot"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {n:>7} 行")
    revenue = conn.execute(
        "SELECT ROUND(SUM(total_amount - discount_amount), 2) FROM orders "
        "WHERE status <> '已取消'"
    ).fetchone()[0]
    print(f"  累计实收（不含取消单）：{revenue}")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=6000)
    ap.add_argument(
        "--url", default="",
        help="postgresql://user:pass@host:5432/db；不填则重建本地 SQLite",
    )
    args = ap.parse_args()
    url = args.url or None
    build(args.orders, url)
    _report(url)


if __name__ == "__main__":
    main()
