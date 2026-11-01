-- PostgreSQL 版 schema：和 SQLite 版同一套业务表，类型按真实数仓的来。
-- 差别只有类型：日期用 date / timestamp、金额用 numeric(12,2)。
-- 字段的含义、口径仍然只写在 app/schema_catalog.py 里（换库不动业务代码）。
--
-- 用法： psql "$DATABASE_URL" -f db/schema_pg.sql

DROP TABLE IF EXISTS refund;
DROP TABLE IF EXISTS order_item;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS inventory_snapshot;
DROP TABLE IF EXISTS product;
DROP TABLE IF EXISTS category;
DROP TABLE IF EXISTS customer;
DROP TABLE IF EXISTS store;

CREATE TABLE store (
    id        integer PRIMARY KEY,
    name      text    NOT NULL,
    city      text    NOT NULL,
    region    text    NOT NULL,              -- 华东 / 华南 / 华北 / 西南
    opened_at date    NOT NULL
);

CREATE TABLE category (
    id          integer PRIMARY KEY,
    name        text    NOT NULL,
    parent_name text
);

CREATE TABLE product (
    id          integer PRIMARY KEY,
    name        text    NOT NULL,
    category_id integer NOT NULL REFERENCES category(id),
    price       numeric(12,2) NOT NULL,      -- 标准售价
    cost        numeric(12,2) NOT NULL,      -- 成本价
    status      text    NOT NULL             -- 在售 / 停售
);

CREATE TABLE customer (
    id            integer PRIMARY KEY,
    name          text    NOT NULL,
    gender        text    NOT NULL,
    birthday      date    NOT NULL,
    city          text    NOT NULL,
    level         text    NOT NULL,          -- 普通 / 银卡 / 金卡
    registered_at date    NOT NULL
);

CREATE TABLE orders (
    id              integer PRIMARY KEY,
    order_no        text    NOT NULL UNIQUE,
    customer_id     integer NOT NULL REFERENCES customer(id),
    store_id        integer NOT NULL REFERENCES store(id),
    status          text    NOT NULL,        -- 已支付 / 已发货 / 已完成 / 已取消
    channel         text    NOT NULL,        -- 门店 / 小程序 / 天猫
    total_amount    numeric(12,2) NOT NULL,  -- 应付金额（优惠前）
    discount_amount numeric(12,2) NOT NULL,  -- 优惠金额
    paid_at         timestamp,               -- 未支付为 NULL
    created_at      timestamp NOT NULL
);

CREATE TABLE order_item (
    id         integer PRIMARY KEY,
    order_id   integer NOT NULL REFERENCES orders(id),
    product_id integer NOT NULL REFERENCES product(id),
    quantity   integer NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    amount     numeric(12,2) NOT NULL
);

CREATE TABLE refund (
    id            integer PRIMARY KEY,
    order_id      integer NOT NULL REFERENCES orders(id),
    order_item_id integer,
    reason        text    NOT NULL,          -- 质量问题 / 七天无理由 / 发错货 / 其他
    amount        numeric(12,2) NOT NULL,
    status        text    NOT NULL,          -- 处理中 / 已退款 / 已驳回
    created_at    timestamp NOT NULL
);

CREATE TABLE inventory_snapshot (
    store_id      integer NOT NULL,
    product_id    integer NOT NULL,
    snapshot_date date    NOT NULL,
    stock         integer NOT NULL,
    sold          integer NOT NULL,
    PRIMARY KEY (store_id, product_id, snapshot_date)
);

CREATE INDEX idx_orders_paid_at  ON orders(paid_at);
CREATE INDEX idx_orders_store    ON orders(store_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_item_order      ON order_item(order_id);
CREATE INDEX idx_item_product    ON order_item(product_id);
CREATE INDEX idx_refund_order    ON refund(order_id);
