# AskData · 自然语言数据分析（ChatBI）

用中文提问，自动生成 SQL、在只读沙箱里执行、把结果画成图。

**在线地址**：<http://8.134.184.59:8130/>（演示库固定种子、每次容器重建重灌，数据日期跟着运行日走）
代码：<https://github.com/xiaopengclassmate/askdata>

![AskData 演示：中文提问 → 生成 SQL → 出图 → 追问继承](docs/screenshots/askdata-demo.gif)

> 简历定位：证明**Python 全栈 + AI 工程化**能力。
> 和现有的 Java 项目（文档问答）不重复——这里考的是结构化数据、schema 理解与执行安全。

## 为什么不是又一个 RAG 项目

| | 常见 RAG 项目 | 本项目 |
|---|---|---|
| 输入 | 文档 | 中文问题 |
| 核心难点 | 切分、召回 | schema 裁剪、SQL 正确性、执行安全 |
| 出问题的方式 | 答不准 | **执行了不该执行的语句 / 查错表 / 把取消单算进营收** |
| 能讲深的地方 | 召回率 | 沙箱、幂等口径、错误自修复 |

「大模型生成的 SQL 是外部输入」——这句话是所有设计的起点。

## 架构

```
                 ┌──────────────────────────────────────────────┐
  中文问题  ───▶ │ /api/ask                                     │
                 │  ① 含糊检测（规则）──▶ 直接反问，不调模型     │
                 │  ② 选表：命中词打分，只投喂相关表 + 口径      │
                 │  ③ 生成 SQL（DashScope / qwen-plus）         │
                 │  ④ 四道关卡                                  │
                 │     安全网关 → 语义层 → EXPLAIN → 只读执行    │
                 │     （执行前按身份建 TEMP VIEW，行级权限生效） │
                 │  ⑤ 报错 / 结果为空 → 回喂模型重写，重试 ≤3 次  │
                 └───────────────┬──────────────────────────────┘
                                 ▼
                   结果 + SQL + 重试日志 + 建议图型
                                 ▼
                 Vue 3 + ECharts（规则选图，不额外调模型）
```

要点：**关卡在模型之外**。模型可以生成任何 SQL，但决定它能不能跑的是代码。

## 当前进度（2026-11-01）

已跑通并验证：

- [x] 演示业务库 8 张表 / 6000 订单 / 10076 明细 / 496 退款（`db/seed.py`，固定种子可复现）
- [x] schema 语义目录 `app/schema_catalog.py`（表 / 字段 / 枚举值 / 业务口径 + 少样本）
- [x] SQL 安全网关 `app/sql_guard.py`：单条 SELECT、关键字黑名单、强制 LIMIT
- [x] 只读连接（`mode=ro`）+ 执行超时中断（`set_progress_handler`）
- [x] 语义层 `semantic_layer/retail.yml`：9 个指标口径 + 时间约定 + 已批准 join
- [x] Text2SQL 主链路：schema 检索 → 生成 → 网关 / 语义 / EXPLAIN 三重校验 → 执行 → 报错自修复
- [x] 多轮会话：`session_id` 带上最近 2 轮上文（追问"那上个月呢"不用重复说）
- [x] 含糊问题反问：既没指标也没维度时先问清楚，不硬猜
- [x] FastAPI 接口：`/health` `/api/schema` `/api/query` `/api/ask` `/api/session/{id}`
- [x] Vue 3 + ECharts 前端（按需引入，构建产物 579 kB），构建后由 FastAPI 直接托管
- [x] 行级权限：按身份过滤门店，用临时视图在数据库层生效，模型全程无感
- [x] 双引擎：SQLite（demo 默认）/ PostgreSQL（`DATABASE_URL` 一键切换），方言、只读、超时、权限各自适配
- [x] 200 条标准 SQL 双引擎结果集逐条比对（`tests/test_dialect_parity.py`）
- [x] 结果自检：SQL 合法但跑出空结果（0 行 / 聚合 NULL）时回喂模型重写
- [x] 前端可「复制 SQL / 导出 CSV」（CSV 带 BOM，Excel 打开中文不乱码）
- [x] 裸 SQL 口 `/api/query` 生产默认关闭（`ENABLE_RAW_SQL=false` 后一律 404）
- [x] 单测 103 条（其中 2 条双引擎比对用例在没接 PostgreSQL 时自动跳过）
- [x] 评测集 200 条：开发集 140 条 + 留出集 60 条，每题带题型标签，跑完按题型 / 按批次归因
- [x] 标准答案自检 `eval/check_questions.py`：不调模型，先证明「题目本身没写错」
- [x] 公网保护：每 IP 限流 + 全局日配额 + 并发闸门（`app/ratelimit.py`）
- [x] 结构化日志：访问日志 + 每问一条（问题 / SQL / 重试 / 耗时 / IP）
- [x] Docker 多阶段构建 + GitHub Actions（push 时跑单测 + 校验评测集）

冒烟结果：合法 join 查询 6 行 0ms；`DROP TABLE`、`SELECT 1; DELETE ...`、`UPDATE` 全部 400 拦截。

待做：

- [ ] 评测集 v2（新增的 120 条）复跑一次，补上分类准确率——本地没有 API key，等有额度时跑

## 目录

```
askdata/
├── app/
│   ├── config.py            # .env 配置
│   ├── db.py                # 数据库访问入口（转给当前引擎）
│   ├── dialect.py           # 当前 SQL 方言（决定提示词怎么说时间）
│   ├── backends/            # 引擎实现：sqlite.py / postgres.py，接口在 base.py
│   ├── schema_catalog.py    # 中文语义目录（喂给模型的 schema 知识）
│   ├── sql_guard.py         # 安全网关（纯函数，好写单测）
│   ├── semantic.py          # 语义层加载 + join 白名单校验
│   ├── clarify.py           # 含糊问题反问（纯规则）
│   ├── session.py           # 多轮会话上下文（内存 + TTL）
│   ├── sanity.py            # 结果自检（SQL 合法但返回空 → 判可疑，回喂重写）
│   ├── permissions.py       # 行级权限（按身份建临时视图，数据库层过滤）
│   ├── ratelimit.py         # 公网保护：每 IP 限流 / 日配额 / 并发闸门
│   ├── logging_setup.py     # 结构化日志（一行一条 key=value）
│   ├── llm.py               # DashScope 兼容接口 + JSON 容错解析
│   ├── text2sql.py          # 选表 → 生成 → 校验 → 执行 → 自修复 → 选图
│   └── main.py              # FastAPI 入口（顺带托管前端构建产物）
├── semantic_layer/
│   └── retail.yml           # 指标口径 / 时间约定 / 已批准 join
├── db/
│   ├── schema.sql           # 8 张表（SQLite）
│   ├── schema_pg.sql        # 同样的 8 张表（PostgreSQL，日期/金额用真实类型）
│   └── seed.py              # 造数（固定种子；--url 时灌进 PostgreSQL）
├── eval/
│   ├── questions.yml        # 140 条开发集 + 标准 SQL（cat: 题型，sql_pg: PostgreSQL 版）
│   ├── questions-holdout.yml# 60 条留出集（冻结，验证泛化）
│   ├── check_questions.py   # 标准答案自检（不调模型）
│   └── run_eval.py          # 结果集比对，按题型 / 按批次归因
├── tests/                   # 103 条单测（含评测集完整性校验）
├── web/                     # Vue 3 + ECharts 前端
├── .github/workflows/ci.yml # push 时跑单测
├── Dockerfile               # node 构建前端 → python 运行时，单镜像
├── Dockerfile.prebuilt      # 小内存服务器用：dist 本地构建好，镜像里不装 node
├── docker-compose.prod.yml  # 线上部署（限内存 + 宿主机 .env 提供密钥）
├── requirements.txt
└── requirements-postgres.txt # 只有切 PostgreSQL 时才装
```

## 跑起来

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/Scripts/python.exe db/seed.py            # 建库灌数
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8130
```

打开 http://127.0.0.1:8130/docs 可以直接调接口。

前端单独开发时：`cd web && npm install && npm run dev`（5175 端口，`/api` 代理到 8130）。

## 部署

```bash
# 单镜像：node 阶段构建前端，python 阶段跑服务，首次启动自动灌演示库
export DASHSCOPE_API_KEY=sk-xxx
docker compose up -d --build
```

镜像里没有任何密钥：`DASHSCOPE_API_KEY` 只从运行环境读，`.env` 在 `.gitignore` 和 `.dockerignore` 里都排除了。

**小内存服务器**（现在线上那台只有 1.7G 内存，还跑着别的服务）：用 `Dockerfile.prebuilt` + `docker-compose.prod.yml`，前端在本地 `npm run build` 好再传，容器里不装 node，`mem_limit: 400m`（实测常驻约 50MB）：

```bash
npm --prefix web run build
tar -czf askdata_deploy.tgz app db semantic_layer web/dist requirements.txt \
    Dockerfile.prebuilt docker-compose.prod.yml   # 别带 db/*.db
# 服务器上：解包 → 写 .env(600) → 起
docker compose -f docker-compose.prod.yml up -d --build
```

两个坑：① 台机器内存紧张，**构建/首次启动那几秒 sshd 会失去响应**（banner exchange 超时），等一两分钟自己恢复，别急着重启；② 容器不挂 volume，重建就重灌演示库——演示数据带相对日期，留旧库会让「最近 7 天」查不到东西。

## 公网保护与可观测性

把带 API key 的服务直接挂到公网，等于把额度送给扫端口的人。所以 `/api/ask` 前有三层，各自挡一种打法（`app/ratelimit.py`）：

| 层 | 挡什么 | 默认 |
|---|---|---|
| 单 IP 每分钟 | 同一个 IP 的脚本猛刷 | ask 10 次/分、query 30 次/分 |
| 全局每日 ask 次数 | 换着 IP 刷——额度用完就停，最坏损失有上界 | 200 次/天（约 600 次模型调用） |
| 同时处理的 ask 数 | 并发打满：单次 2~3 秒，1.7G 的机器扛不住几十个并发 | 4 个 |

几个刻意的选择：

- **超限返回 429 + `Retry-After`**，不是排队。排队只会把请求堆在内存里，越堆越多，最后一起超时。
- **默认不信 `X-Forwarded-For`**：这个头谁都能伪造，信了就等于给限流开后门。前面真挂了 Nginx 再用 `TRUST_PROXY_HEADERS=true` 打开。
- **状态放进程内**：线上是单容器单进程（uvicorn 默认 1 worker），多 worker / 多实例时这套要挪到 Redis（`INCR + EXPIRE` 或 ZSET 滑动窗口）。知道边界在哪，比现在就多部署一个组件重要。
- `GET /health` 会带上 `daily_quota_left`，看一眼就知道今天是不是被人刷过。
- **裸 SQL 口 `/api/query` 线上关掉**（`ENABLE_RAW_SQL=false`，返回 404 而不是 403——不告诉对方「这里有东西只是被禁了」）。它只服务本地调试和压测沙箱，对公网没有任何业务价值。

日志一行一条，`docker logs askdata` 直接读（时间是 UTC，带 `Z` 后缀——容器默认 UTC，本地开发是北京时间，不标出来最容易看错）：

```
2026-11-01T07:58:37Z INFO  http status=200 method=POST path=/api/ask ip=1.2.3.4 elapsed_ms=2083
2026-11-01T07:58:37Z INFO  ask ok=True quota_used=3/200 session=4d8c6fcca242 ip=1.2.3.4
                           attempts=1 rows=1 elapsed_ms=2077 query_ms=30
                           q="库存总量是多少" sql="SELECT SUM(stock) ... WHERE snapshot_date = (SELECT MAX(...))"
2026-11-01T07:58:37Z WARNING ask limited=daily_quota ip=1.2.3.4 reason=今日演示额度（200 次）已用完，明天再来
```

带 question 和 SQL 的原因很实际：线上答错了，光看状态码 `200` 没有任何信息量；有这两样，不登服务器也能复现。

## 行级权限

总部要全量，区域经理要自己片区的，店长只能看本店——这是 Text2SQL 上生产必过的一关，而且比普通后台更难：**SQL 是模型写的，你没法在代码里补 WHERE**。

demo 里三个身份，通过 `x-demo-role` 请求头切换（前端右上角下拉框），不传就是总部：

| 身份 | 可见门店 |
|---|---|
| 总部 `hq`（默认） | 全部 6 家 |
| 华南区经理 `south` | 深圳、广州 |
| 深圳南山店店长 `sz-nanshan` | 深圳南山店 |

做法是**让数据库来过滤，而不是改 SQL**（`app/permissions.py`）：

```sql
CREATE TEMP VIEW orders AS SELECT * FROM main.orders WHERE store_id IN (1);
```

temp schema 优先级高于 main，所以模型生成的 `FROM orders` 会被 SQLite 自己重定向到过滤后的视图。`order_item` / `refund` 没有 `store_id`，顺着 `orders` 一起收窄：

```sql
CREATE TEMP VIEW order_item AS
  SELECT * FROM main.order_item
  WHERE order_id IN (SELECT id FROM main.orders WHERE store_id IN (1));
```

为什么不走另外两条路：

- **靠提示词让模型自己加 `WHERE store_id = 1`**：模型会忘，换个问法就丢（「各门店营收」它根本不会加），被注入还能直接绕。权限放在模型能力里，等于没有。
- **拿到 SQL 之后字符串改写/拼接条件**：子查询、CTE、UNION、`JOIN ... ON` 嵌套，手写注入条件总有覆盖不到的写法，漏一个就是越权。而且改写过的 SQL 和展示给用户看的 SQL 不一致，排查问题时会打架。

TEMP VIEW 的好处是这条边界**不依赖模型的正确性**：模型写 `SELECT COUNT(*) FROM orders` 还是写三层嵌套 CTE，出来的都只有本店数据。

几个细节：

- **视图只在受限身份时建**，总部什么都不做——没人看得到过滤逻辑，也没有额外开销。
- **TEMP 对象是连接级的**：每次查询新开连接、建视图、关连接，不会串到下一个请求（有单测专门钉这一条）。
- **视图不可写**：想在 `orders` 上 `DELETE` 直接被 SQLite 拒，越权写这条路一起堵了。
- 身份在 `/api/ask` 和 `/api/query` 都生效；`customer` / `product` / `category` 是全局维表，任何身份都能全看。
- 响应里带 `scope` 字段，前端据此显示「当前身份：深圳南山店店长 —— 只统计你有权限的门店」，避免把部分数据当全量。
- 未知身份返回 400 而不是悄悄降级成总部——**权限出问题时宁可报错，不能默认放行**。

真实系统里身份来自登录态（用户 → 组织架构 → 可见门店），`DEMO_ROLES` 这份映射不该出现在代码里；临时视图这套换成 PostgreSQL 也照样能用（见下一节）。

## 换引擎：SQLite → PostgreSQL

demo 默认 SQLite 是为了零依赖能跑，但真实业务都在 PostgreSQL / 数仓里。**换库不该是重写**，所以存储层先抽了一层（`app/backends/`），三个方法定契约，各自实现：

| | SQLite | PostgreSQL |
|---|---|---|
| 怎么连 | `mode=ro` 打开文件 | 连接 + 会话级 `default_transaction_read_only = on` |
| 超时 | `set_progress_handler` 自己数指令 | `SET statement_timeout`，服务端到点中断 |
| 行级权限 | `CREATE TEMP VIEW`（temp schema 优先于 main） | 同样是临时视图，但要看 `search_path` 认 pg_temp |
| 编译器报错 | `EXPLAIN` | `EXPLAIN (FORMAT JSON)` |
| 类型 | REAL / TEXT | numeric / date / timestamp（返回值归一化成 float / ISO 字符串） |

切换只要三行：

```bash
pip install -r requirements-postgres.txt
psql "$DATABASE_URL" -f db/schema_pg.sql          # 或直接 python db/seed.py --url ...
export DATABASE_URL=postgresql://user:pass@host:5432/askdata
```

**方言要跟着换，不只是驱动。** 模型只会照着喂给它的写法写 SQL，提示词里不换，它会在 PostgreSQL 上生成 `date('now','-30 days')`。所以：

- 系统提示里的引擎名、`semantic_layer/retail.yml` 里的时间口径（`@time` 占位符按引擎展开）和示例 SQL 都分两份
- 评测集里同一条题有 `sql` 和 `sql_pg` 两个标准答案，跑哪套看当前引擎

**怎么证明换过去没换错**（`tests/test_dialect_parity.py`）：评测集 200 条标准 SQL 在两套引擎上逐条执行，**结果集必须完全一致**——行数、每一行的值都对。这不是「SQL 改得像」，是同一批数据同一批问题必须出同一个答案。

一个真实的坑：SQLite 的 `date('now','-30 days')` 是**日期边界**（当天零点），PostgreSQL 照直翻译成 `now() - interval '30 days'` 会带上当前时刻，少算半天，差出几十行。PG 版得写 `current_date - 30`。这种差异只有把两边跑一遍才看得见。

另一个：会话时区固定 UTC（`DB_TIMEZONE`）。SQLite 的 `now` 本来就是 UTC，两边不对齐，「最近 N 天」在晚上八点之后就会差一天。

> 线上容器仍然用 SQLite：那台 1.7G 的机器再起一个 PG 实例没必要，demo 的数据量也吃不出差别。双引擎的价值在于「换库是改配置，不是改代码」，而不是为了演示而演示。

## 业务口径（写在这里，也写进了 schema 目录）

这几条是模型最容易错、面试官最爱问的地方：

- **实收** = `total_amount - discount_amount`，且必须排除 `status = '已取消'`
- **成交价**用 `order_item.unit_price`，不是 `product.price`
- 时间筛选一律用 `orders.paid_at`（取消单为 NULL），不用 `id`
- **退款金额**只统计 `refund.status = '已退款'`
- 退款率 = 退款笔数 / 已完成订单数

## 多轮与反问

多轮不是"把聊天记录拼进 prompt"。这里只给模型最近 2 轮的**问题和 SQL**，并写死一条规则：本次省略的条件从上文继承，只替换说出来的部分。

| 第一句 | 追问 | 继承 / 替换 |
|---|---|---|
| 上个月各门店的营收 | 那华南区呢 | 继承指标和时间，只换筛选 |
| 最近 30 天销量前 5 的商品 | 换成按品类汇总 | 继承指标和时间，只换分组 |
| 各渠道的订单量 | 那客单价呢 | 继承维度和范围，只换指标 |
| 今年的总营收 | 去年呢 | 继承指标，只换时间 |

含糊问题的处理是**规则判定 + 给可点选项**，不让模型决定"要不要问"：

| 类型 | 例子 | 处理 |
|---|---|---|
| 只有意图没有对象 | 「最近怎么样」 | 反问指标 / 时间 / 维度，附 4 个可点选项 |
| 追问但没上文 | 「那上个月呢」作为第一句 | 提示这是新会话，先问第一句 |
| 指标有歧义 | 「退款多少」 | 金额 / 笔数 / 退款率 三选一 |
| 口径已说死 | 「处理中的退款有几笔」 | 不反问，直接查 |

## 设计要点

1. **schema 不进数据库注释，单独一份 Python 目录**：SQLite 丢注释，且模型要的是业务口径不是类型。
2. **沙箱是双保险**：语法白名单 + 只读连接。即使模型被注入，最坏也只能读到该看的数据。
3. **强制 LIMIT**：防止 `SELECT *` 把大表打回内存，也防止前端被百万行拖死。
4. **错误自修复**：执行报错时把数据库原文回喂模型重试（最多 3 次尝试），比调 prompt 更有效。
5. **列形状单独成段、贴在问题后面**：问「哪个最」该出几列几行、问「前 5」该不该带指标列，这套规则放系统提示里模型经常不听话，贴近问题的位置才稳定（见下方评测里的 `SHAPE_HINT`）。
6. **结果自检**：报错自修复只管「跑不起来」，但更常见的错法是 SQL 合法、跑出来是空的——聚合被 WHERE 全过滤掉返回 `NULL`，或者筛选值写成了库里不存在的叫法（模型很爱把「线上」扩写成「线上渠道」）。这两种没有任何异常可回喂，所以加一道按**结果**判的检查：0 行 / 单值 `NULL` 就判可疑，把原因当错误提回去让它重写。刻意判得很窄——`0`、负数、并列、超预期的大数一律不管，那些要么合法，要么得靠业务知识才判得了，硬判必然误伤正确答案。
7. **权限下沉到数据库，不进提示词、不改写 SQL**：模型生成的 SQL 不该和用户身份绑定。用临时视图让数据库自己把 `FROM orders` 改道到过滤后的视图，模型无感、不会被绕、也不存在「拼接漏了一种写法」的越权口子（详见上方「行级权限」）。
8. **换库是换配置，不是重写**：存储层抽成 `execute / dry_plan / table_stats` 三个方法，SQLite 和 PostgreSQL 各自实现；连**提示词里的方言**也跟着引擎走，否则换库之后模型会照着老语法写。验证方式不是「看起来能跑」，而是 200 条标准 SQL 在两边逐条比对结果集（详见「换引擎」）。

## 评测结果（2026-11-01 定稿）

**评测集 200 条**：开发集 140（`eval/questions.yml`）+ 留出集 60（`eval/questions-holdout.yml`），每条题都带题型标签，跑完的 JSON 里按**题型**和**批次**（v1 = 最早那 60 条，v2 = 后扩的 120 条）分开统计：

| 题型 | 考什么 |
|---|---|
| `time` | 上月 / 今年 / 去年 / 最近 N 天 / 按月按天 / 时段 |
| `metric` | 实收 vs 成交额、客单价、成本毛利、退款金额 |
| `group` | 分组与维度下钻 |
| `topn` | Top-N、最大最小、HAVING 阈值 |
| `ratio` | 占比、取消率、退款率、复购率 |
| `join` | 三张表以上、退款链路 |
| `negate` | 没下过单 / 没有退款 / 排除某区域 |
| `snapshot` | 库存只能取最新一天 |
| `customer` | 会员等级、性别、城市、复购 |
| `compare` | 差值、倍数、两期对比 |

这样错了能说清楚「错在哪类」，而不是只有一个笼统的准确率。

**先证明题目本身没写错，再测模型**：`eval/check_questions.py` 不调模型，把每条标准 SQL 真跑一遍，查跑不起来 / 0 行 / 单值 `NULL` / 题干重复 / 缺题型标签 / 时间题漏了 PG 版这几种毛病。扩题时最隐蔽的坑不是模型答错，而是**标准答案写错**——那样评测会永远判错，还看不出原因。200 条现在 0 问题，CI 里也跑了这一步。

判定口径：**结果集完全一致**才算对（列和行都要对上，不看 SQL 长得像不像）。

下面是**已测过**的数字，都是 v1 那 60 条 / 20 条上的成绩：

| 模式 | 通过 | 准确率 | 平均耗时 |
|---|---|---|---|
| baseline：裸 schema，无语义层、无校验、不重试 | 47/60 | 78.3% | 2.34s |
| full：语义层 + 三重校验 + 自修复 + 结果自检（最多 3 次尝试） | 60/60 | **100%** | 2.98s |

> 加结果自检之后重跑过一遍：开发集仍然 60/60、留出集仍然 19/20，没有回退。平均耗时从 2.68s 涨到 2.98s——一次 `ask` 最多 2~3 次模型调用，这个量级里分不清是自检多了一轮重写还是模型侧抖动，不做过度解读。

> **口径说明**：这 60 条题在开发过程中被用来定位问题、调 prompt 规则，所以 full 的 100% 是「在这套题上」的成绩，不代表任意新问题的准确率。更要看的是 baseline——同一套题、同一个模型、同一份 schema 检索，唯一变量是语义层 + 三重校验 + 自修复，78.3% → 100% 才是这套设计带来的增量。

**列形状是第二个隐形得分点**。中途 full 一度停在 86.7%，8 条失败**没有一条是算错数**，全是答案形状不同：问「哪个门店订单最多」答了门店名 + 订单数（多一列）、「线上渠道」vs「线上」（标签措辞）、「覆盖了哪几天」vs「几天」（问法歧义）。把这些规则塞进系统提示，模型经常不听话；最后单独拎成一段 `SHAPE_HINT` 贴在**问题后面**（紧邻问题遵循度更高）才稳定：

- 只问「哪个 / 哪家……最」且没有数量 → 只返回对象一列、1 行
- 带数量（前 5 / Top 3 / 最高的 N 个）→ 维度列 + 指标列、正好 N 行
- 各 / 每个 → 维度列 + 指标列、全部分组
- 追问只是换筛选条件（「那华南区呢」「只看 X」）→ 这不是在问「哪个」，列和分组沿用上一轮

代价是改规则会互相打架：加「只返回对象一列」之后，「前 5」和对比题立刻丢列，来回调了三轮才收敛——这也是每次改完都重跑 full + baseline 的原因。

**留出集**（`eval/questions-holdout.yml`，共 60 条；已测的是最早那 20 条，写完后冻结、没有参与过任何提示词调整）：

| 通过 | 准确率 | 说明 |
|---|---|---|
| 19/20 | **95.0%** | 唯一失败：「停售商品占全部商品的比例」模型答 6.67（百分数），标准答案是 0.0667（比率）——单位口径分歧，不是算错 |

这套题就是为了回答「100% 是不是调出来的」：同一份提示词、不改一个字直接跑，能到 95%。差的 5 个点集中在**问法本身有歧义**（比例还是百分比）和**同数并列**（哪个品类商品最多）这类地方，和模型算错关系不大。

> 诚实记录：留出集**第一次跑是 17/20**，其中 2 条是题出得有问题——「哪个品类的商品最多」5 个品类各 6 个商品、并列没有唯一答案；「退款笔数最多的商品」按项目自己的口径（退款笔数是申请笔数，不按 status 过滤）前两名都是 26 笔，同样并列。这两条换成了有唯一答案的问法后整套重跑，才得到 19/20。换过题这件事写在这里，不藏着；但不是因为模型答错才换的。

> **扩出来的 120 条（v2）还没有模型侧成绩**。2026-11-01 把评测集从 80 条扩到 200 条，做完的是「标准答案自检 0 问题 + 两引擎结果集逐条一致」；本地没有可用的 API key，所以准确率这一栏空着，不估、不编。有额度时跑 `python eval/run_eval.py`（开发集）和 `python eval/run_eval.py --questions questions-holdout.yml --out result-holdout.json`（留出集），JSON 里直接有按题型的拆解。

**多轮追问**（`eval/multiturn.yml`，6 组两轮对话，只看最后一轮）：

| 通过 | 准确率 | 说明 |
|---|---|---|
| 5/6 | 83.3% | 失败的一条：问「各渠道的订单量」→「那客单价呢」，模型丢了"按渠道"这个维度，答了整体客单价 |

这条失败很典型：**追问里省略的维度最难继承**，因为模型容易把它当成一个全新的问题。试过把「把上文 SQL 当作起点改写」写进提示，能修好「只看家用电器」这类，但「那客单价呢」仍然会丢维度。

baseline 的 13 条失败里，典型错法是：营收漏排除取消单返回 NULL、退款率算成 7.4 而不是 0.08、库存把 30 天日终快照全累加（91151 vs 2986，放大 30 倍）、`p.category` 字段名瞎猜导致编译失败。这几类 full 全拦住了——前三类靠语义层，最后一类靠 `EXPLAIN` 预编译。

## 简历草稿

> **AskData — 自然语言数据分析平台**（个人项目，2026.10，Python 全栈）
> FastAPI + Vue 3 + ECharts + SQLite。中文提问 → 生成 SQL → 只读沙箱执行 → 自动出图。
> · 建语义层把 7 个指标口径（公式 + 强制过滤条件）从代码抽成配置，模型只能从已定义口径里挑；配三重校验（安全网关 / 语义 join 白名单 / EXPLAIN 预编译）与报错回喂自修复（≤3 次），自建 200 题评测集（按题型归因，其中 60 题完成同题消融 78.3% → 100%）
> · 执行安全不依赖模型听话：只读连接 `mode=ro` + 单条 SELECT 白名单 + 强制 LIMIT + `set_progress_handler` 超时中断，DDL/DML、多语句拼接全部拦截
> · 多轮追问与含糊反问：带最近 2 轮问题 + SQL 做条件继承，模型只替换说出来的部分；问题缺指标 / 缺上文 / 口径歧义时反问并给可点选项，不让模型决定"要不要问"
> · 检索式 schema 裁剪 + 规则选图（不额外调模型），前端按需引入 ECharts 把包体从 1.1 MB 压到 577 kB，构建产物由 FastAPI 直接托管，单镜像部署
