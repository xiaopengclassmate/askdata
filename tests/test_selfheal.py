"""自修复回路：用假模型把「结果为空 → 回喂重写」这条路走一遍。

真模型跑一次 2~3 秒还费额度，这里只验证**链路的控制流**：什么时候重试、
回喂里带不带线索、次数用完之后吐不吐 warning。SQL 是真的打到演示库上执行的。
"""

import json

from app import llm, text2sql


def _fake(answer_sqls: list[str], calls: list[str]):
    """按顺序吐出预设的 SQL；返回第 n 次调用时模型看到的 prompt。"""

    def complete(system: str, user: str) -> str:
        calls.append(user)
        sql = answer_sqls[min(len(calls) - 1, len(answer_sqls) - 1)]
        return json.dumps({"sql": sql, "explanation": "", "chart": "metric"})

    return complete


def test_empty_result_triggers_rewrite_with_hint(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        llm,
        "complete",
        _fake(
            [
                # 第一次：库里没有「线上渠道」这个取值 → SUM 返回 NULL
                "SELECT SUM(total_amount) AS revenue FROM orders WHERE channel = '线上渠道'",
                # 第二次：改回原始取值
                "SELECT SUM(total_amount) AS revenue FROM orders WHERE channel = '门店'",
            ],
            calls,
        ),
    )

    result = text2sql.answer("线上渠道的销售额是多少")

    assert result["ok"] is True
    assert result["attempts"] == 2
    assert len(calls) == 2
    # 第二次调用时，prompt 里必须带着「上一次是空的」这条线索，否则模型不知道要改
    assert "0 行" in calls[1] or "NULL" in calls[1]
    assert "线上渠道" in calls[1]
    assert any("结果自检" in line for line in result["retry_log"])
    assert result["warning"] == ""


def test_gives_up_but_returns_result_with_warning(monkeypatch):
    """次数用完了还空着：照常返回结果，用 warning 明说它可疑，而不是报错什么也不给"""
    calls: list[str] = []
    monkeypatch.setattr(
        llm,
        "complete",
        _fake(
            ["SELECT SUM(total_amount) AS revenue FROM orders WHERE channel = '线上渠道'"],
            calls,
        ),
    )

    result = text2sql.answer("线上渠道的销售额是多少")

    assert result["ok"] is True
    assert result["attempts"] == text2sql.MAX_ATTEMPTS
    assert len(calls) == text2sql.MAX_ATTEMPTS
    assert "线上渠道" in result["warning"]


def test_normal_answer_does_not_retry(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        llm,
        "complete",
        _fake(["SELECT COUNT(*) AS n FROM orders"], calls),
    )

    result = text2sql.answer("一共有多少订单")

    assert result["attempts"] == 1
    assert len(calls) == 1
    assert result["warning"] == ""
