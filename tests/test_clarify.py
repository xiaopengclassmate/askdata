"""含糊问题反问：该问的必须问，评测集那 60 条一句都不能误伤"""

from pathlib import Path

import yaml

from app import clarify

QUESTIONS = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "eval" / "questions.yml").read_text(encoding="utf-8")
)


def test_vague_question_gets_clarified():
    assert clarify.need_clarify("最近怎么样") is not None
    assert clarify.need_clarify("帮我分析一下") is not None


def test_too_short_is_clarified():
    assert clarify.need_clarify("查一下") is not None
    assert clarify.need_clarify("") is not None


def test_specific_question_passes():
    for q in [
        "上个月各门店的营收",
        "退款率是多少",
        "最近 30 天卖得最好的商品",
        "各渠道的订单量和客单价",
    ]:
        assert clarify.need_clarify(q) is None, q


def test_clarify_message_lists_options():
    msg = clarify.need_clarify("最近怎么样")
    assert "指标" in msg and "时间" in msg


def test_no_eval_question_is_intercepted():
    """评测集是同一套题跑的，反问一触发就会让两组数字不可比"""
    blocked = [item["q"] for item in QUESTIONS if clarify.need_clarify(item["q"])]
    assert blocked == []
