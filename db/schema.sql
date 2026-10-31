-- 演示业务库：连锁零售电商（8 张表，够支撑 join / 聚合 / 时间口径类问题）
-- 说明：SQLite 不保留 COMMENT，表和字段的中文语义放在 app/schema_catalog.py
--       里，喂给模型时用那份（以后换 MySQL 也不用改业务代码）。

DROP TABLE IF EXISTS refund;
DROP TABLE IF EXISTS order_item;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS inventory_snapshot;
DROP TABLE IF EXISTS product;
DROP TABLE IF EXISTS category;
DROP TABLE IF EXISTS customer;
DROP TABLE IF EXISTS store;

CREATE TABLE store (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    region      TEXT    NOT NULL,          -- 华东 / 华南 / 华北 / 西南
    opened_at   TEXT    NOT NULL
);

CREATE TABLE category (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    parent_name TEXT
);

CREATE TABLE product (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    category_id INTEGER NOT NULL,
    price       REAL    NOT NULL,          -- 标准售价
    cost        REAL    NOT NULL,          -- 成本价
    status      TEXT    NOT NULL,          -- 在售 / 停售
    FOREIGN KEY (category_id) REFERENCES category(id)
);

CREATE TABLE customer (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    gender        TEXT    NOT NULL,
    birthday      TEXT    NOT NULL,
    city          TEXT    NOT NULL,
    level         TEXT    NOT NULL,        -- 普通 / 银卡 / 金卡
    registered_at TEXT    NOT NULL
);

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    order_no        TEXT    NOT NULL UNIQUE,
    customer_id     INTEGER NOT NULL,
    store_id        INTEGER NOT NULL,
    status          TEXT    NOT NULL,      -- 已支付 / 已发货 / 已完成 / 已取消
    channel         TEXT    NOT NULL,      -- 门店 / 小程序 / 天猫
    total_amount    REAL    NOT NULL,      -- 应付金额（优惠前）
    discount_amount REAL    NOT NULL,      -- 优惠金额
    paid_at         TEXT,                  -- 未支付为 NULL
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customer(id),
    FOREIGN KEY (store_id)    REFERENCES store(id)
);

CREATE TABLE order_item (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity   INTEGER NOT NULL,
    unit_price REAL    NOT NULL,
    amount     REAL    NOT NULL,
    FOREIGN KEY (order_id)   REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES product(id)
);

CREATE TABLE refund (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    order_item_id INTEGER,
    reason        TEXT    NOT NULL,        -- 质量问题 / 七天无理由 / 发错货 / 其他
    amount        REAL    NOT NULL,
    status        TEXT    NOT NULL,        -- 处理中 / 已退款 / 已驳回
    created_at    TEXT    NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE inventory_snapshot (
    store_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    snapshot_date TEXT    NOT NULL,
    stock         INTEGER NOT NULL,
    sold          INTEGER NOT NULL,
    PRIMARY KEY (store_id, product_id, snapshot_date)
);

CREATE INDEX idx_orders_paid_at   ON orders(paid_at);
CREATE INDEX idx_orders_store     ON orders(store_id);
CREATE INDEX idx_orders_customer  ON orders(customer_id);
CREATE INDEX idx_item_order       ON order_item(order_id);
CREATE INDEX idx_item_product     ON order_item(product_id);
CREATE INDEX idx_refund_order     ON refund(order_id);
