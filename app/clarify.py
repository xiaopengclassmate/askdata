"""问题不清楚时先反问，别硬猜。

真实使用里最伤体验的不是"查不出来"，是"查出来一个看起来对、其实答的是另一个问题"。
所以这类问题直接反问，并给出**可以点的选项**——让用户点一下就把问题补全，
比让他再想一句完整的话要快得多。

四种场景：
  1. missing_metric  太短 / 只有意图没有对象（"帮我看看""最近怎么样"）
  2. no_context      是追问，但会话里没有上文（"那上个月呢" 作为第一句）
  3. ambiguous       指标本身有歧义（"退款多少"是金额还是笔数）
  4. 明确 → 不反问

规则判断，不调模型：反问本身必须是确定性的——模型不该决定"要不要问"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import semantic
from .schema_catalog import TABLE_DOCS


@dataclass
class Clarify:
    kind: str
    message: str
    options: list[dict] = field(default_factory=list)


TIME_WORDS = (
    "今天", "昨天", "前天", "最近", "本周", "上周", "本月", "上月", "上个月", "下个月",
    "今年", "去年", "季度", "每天", "每周", "每月", "年度", "月度", "过去",
)

DIM_WORDS = (
    "门店", "店铺", "渠道", "城市", "区域", "品类", "分类", "商品", "产品",
    "客户", "用户", "地区", "原因", "状态",
)

VAGUE_SIGNALS = ("怎么样", "如何", "表现", "情况", "分析一下", "看看", "帮我查")

# 追问的开头/结尾：没有上文时这类问题无法理解
ANAPHORA = re.compile(r"^(那|换|再看|再按|改看|它|这个|那个)|呢$")

# 问题里已经把这些口径说死了，就不再追问
SPECIFIED_WORDS = (
    "金额", "笔数", "数量", "率", "总量", "最少", "最多", "前 5", "前5",
    "处理中", "已退款", "已驳回", "多少笔", "几笔", "多少件",
)

# 指标歧义：同一个词对应两个不同口径，答错了比答不出更糟
AMBIGUOUS = {
    "退款": (
        "「退款」你想看哪一种？两口径差很多。",
        [
            {"label": "退款金额（只算已退款）", "question": "退款金额是多少"},
            {"label": "退款笔数（含处理中）", "question": "退款笔数是多少"},
            {"label": "退款率", "question": "退款率是多少"},
        ],
    ),
    "库存": (
        "「库存」你指哪一种？",
        [
            {"label": "库存总量", "question": "各门店的库存总量"},
            {"label": "库存最少的 5 个商品", "question": "库存最少的 5 个商品"},
            {"label": "昨天卖了多少件", "question": "昨天全部门店的总销量"},
        ],
    ),
    "卖得": (
        "「卖得怎么样」按哪个指标看？",
        [
            {"label": "销量（件数）", "question": "最近 30 天销量前 5 的商品"},
            {"label": "实收金额", "question": "最近 30 天各商品的实收金额"},
            {"label": "订单量", "question": "最近 30 天每天的订单量"},
        ],
    ),
}

DEFAULT_OPTIONS = [
    {"label": "上个月各门店的实收", "question": "上个月各门店的实收金额"},
    {"label": "最近 30 天销量前 5", "question": "最近 30 天销量前 5 的商品"},
    {"label": "各渠道的订单量", "question": "各渠道的订单量"},
    {"label": "退款率", "question": "退款率是多少"},
]

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


def _ambiguous_shape(q: str) -> bool:
    """问法本身是否含糊：很短，或问的是"多少 / 怎么样"而不是"哪个 / 多少笔" """
    return len(q) <= 8 or q.endswith(("多少", "怎么样", "如何", "情况", "呢", "吗"))


def need_clarify(question: str, has_history: bool = False) -> Clarify | None:
    """返回需要反问的内容；不需要反问时返回 None。"""
    q = (question or "").strip()
    if not q:
        return Clarify("missing_metric", "想问什么？选一个开始，或者直接描述指标和时间。",
                       DEFAULT_OPTIONS)

    has_dim = any(w in q for w in DIM_WORDS)
    has_metric = _metric_hit(q)
    has_schema = _schema_hit(q)

    # 1) 追问但会话里没有上文——"那上个月呢" 单独发过来是没法答的
    if not has_history and ANAPHORA.search(q) and len(q) <= 12 and not has_metric:
        return Clarify(
            "no_context",
            "这句是接着上一句问的，但我这边没有上文（新会话）。"
            "先告诉我第一句要查什么，后面就能直接说「那华南区呢」了。",
            DEFAULT_OPTIONS,
        )

    # 2) 指标本身有歧义，且问题里没有把口径说死
    #    只对「短且含糊」的问法生效——「库存快照覆盖了哪几天」虽然带"库存"，但问的是明确的
    specified = any(w in q for w in SPECIFIED_WORDS)
    if not has_history and not specified and _ambiguous_shape(q):
        for key, (message, options) in AMBIGUOUS.items():
            if key in q and not has_metric:
                return Clarify("ambiguous", message, options)

    # 「去年呢」这类只有时间词的追问，有上文时是完整的（指标从上文继承），不能拦
    short_follow_up = has_history and any(w in q for w in TIME_WORDS)
    if len(q) <= 4 and not short_follow_up:
        return Clarify(
            "missing_metric",
            "问题太短了，我不确定你想看哪个指标。可以直接点一个，或者补充「指标 + 时间 + 维度」。",
            DEFAULT_OPTIONS,
        )

    # 3) 只有意图，没有对象
    if not (has_metric or has_schema or has_dim) and any(s in q for s in VAGUE_SIGNALS):
        return Clarify(
            "missing_metric",
            "这个问题里没有具体的指标和范围，我先确认一下再查，免得答错。"
            "可以补充：① 看哪个指标（实收金额 / 订单量 / 销量 / 退款率 / 客单价 / 库存）"
            "② 什么时间（上个月 / 最近 30 天 / 今年）③ 按什么拆（门店 / 渠道 / 品类）。",
            DEFAULT_OPTIONS,
        )

    return None
