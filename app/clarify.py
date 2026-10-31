"""含糊问题先反问，别硬猜。

真实使用里最伤体验的不是"查不出来"，是"查出来一个看起来对、其实答的是另一个问题"。
所以问题里既没有指标、也没有维度、也没有时间词时，直接反问一句比生成一条 SQL 更合适。

规则判断，不调模型：反问本身必须是确定性的——模型不该决定"要不要问"。
"""

from __future__ import annotations

import re

from . import semantic
from .schema_catalog import TABLE_DOCS

# 时间词：有了它至少能定位时间范围
TIME_WORDS = (
    "今天", "昨天", "前天", "最近", "本周", "上周", "本月", "上月", "上个月", "下个月",
    "今年", "去年", "季度", "每天", "每周", "每月", "年度", "月度", "过去",
)

# 维度词：有了它至少能知道按什么拆
DIM_WORDS = (
    "门店", "店铺", "渠道", "城市", "区域", "品类", "分类", "商品", "产品",
    "客户", "用户", "地区", "原因", "状态",
)

VAGUE_SIGNALS = ("怎么样", "如何", "表现", "情况", "分析一下", "看看", "帮我查")

_HAN = re.compile(r"[一-鿿]")


def _schema_hit(question: str) -> bool:
    """问题里是否出现了任一表 / 字段 / 枚举的中文说明里的词（2 字以上）"""
    words: set[str] = set()
    for _table, doc in TABLE_DOCS.items():
        words.add(doc["name"])
        words.update(doc["columns"].values())
        words.update(doc.get("enums", []))
    compact = "".join(_HAN.findall(question))
    return any(w and w in compact for w in words if len(w) >= 2)


def _metric_hit(question: str) -> bool:
    return bool(semantic.metric_names_hit(question))


def need_clarify(question: str) -> str | None:
    """返回反问文案；不需要反问时返回 None。"""
    q = (question or "").strip()
    if not q:
        return "想问什么？比如：「上个月各门店的实收金额」。"
    if len(q) <= 4:
        return "问题太短了，我不确定你想看哪个指标。比如：「上个月各门店的实收金额」。"

    has_dim = any(w in q for w in DIM_WORDS)
    has_metric = _metric_hit(q)
    has_schema = _schema_hit(q)

    # 「最近怎么样」带时间词但仍然没说看什么——含糊信号优先，其它词兜底
    if not (has_metric or has_schema or has_dim) and any(s in q for s in VAGUE_SIGNALS):
        return (
            "这个问题里没有具体的指标和范围，我先确认一下再查，免得答错。"
            "可以补充：① 看哪个指标（实收金额 / 订单量 / 销量 / 退款率 / 客单价 / 库存）"
            "② 什么时间（上个月 / 最近 30 天 / 今年）③ 按什么拆（门店 / 渠道 / 品类）。"
        )
    return None
