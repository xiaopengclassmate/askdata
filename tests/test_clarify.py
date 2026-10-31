"""反问：该问的必须问，评测集那 60 条一句都不能误伤"""

from pathlib import Path

import yaml

from app import clarify

QUESTIONS = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "eval" / "questions.yml").read_text(encoding="utf-8")
)


def test_vague_question_gets_clarified():
    hint = clarify.need_clarify("最近怎么样")
    assert hint is not None and hint.kind == "missing_metric"
    assert "指标" in hint.message and "时间" in hint.message


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


def test_follow_up_without_history_is_caught():
    """「那上个月呢」单独发过来，没有上文时没法答"""
    hint = clarify.need_clarify("那上个月呢")
    assert hint is not None and hint.kind == "no_context"


def test_follow_up_with_history_passes():
    assert clarify.need_clarify("那上个月呢", has_history=True) is None
    assert clarify.need_clarify("换成华南区呢", has_history=True) is None


def test_ambiguous_metric_is_caught():
    hint = clarify.need_clarify("退款多少")
    assert hint is not None and hint.kind == "ambiguous"
    assert any("退款金额" in o["label"] for o in hint.options)


def test_specified_metric_is_not_caught():
    """口径已经说死了就别问"""
    assert clarify.need_clarify("处理中的退款有几笔") is None
    assert clarify.need_clarify("退款金额最高的商品") is None


def test_options_are_clickable_questions():
    hint = clarify.need_clarify("帮我分析一下")
    assert hint.options
    for o in hint.options:
        assert set(o) == {"label", "question"}
        # 点选项 = 直接提交这句，它自己不能再触发反问
        assert clarify.need_clarify(o["question"]) is None, o["question"]


def test_no_eval_question_is_intercepted():
    """评测集是同一套题跑的，反问一触发就会让两组数字不可比"""
    blocked = [item["q"] for item in QUESTIONS if clarify.need_clarify(item["q"])]
    assert blocked == []
