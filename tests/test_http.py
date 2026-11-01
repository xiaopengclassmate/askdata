"""接口层冒烟：单测都在测模块，这里测「HTTP 这一层」的契约。

以前所有测试都是直接调函数，接口层的错误码、字段名、CORS、开关这类东西
一条都没覆盖——参数名改错、路由挂了单测全绿，线上照样 500。
"""

import json

from fastapi.testclient import TestClient

from app import llm, main, ratelimit
from app.config import settings

client = TestClient(main.app)


def _fresh_limiter(monkeypatch):
    """每个用例用独立的限流器，避免上一个用例把名额刷光。"""
    monkeypatch.setattr(ratelimit, "limiter", ratelimit.RateLimiter())


def test_health_reports_quota():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "daily_quota_left" in body
    assert "orders" in body["tables"]


def test_schema_endpoint_serves_prompt_material():
    body = client.get("/api/schema").json()
    assert set(body) == {"schema", "examples", "semantic"}
    assert "orders" in body["schema"]


def test_query_runs_select(monkeypatch):
    _fresh_limiter(monkeypatch)
    resp = client.post("/api/query", json={"sql": "SELECT COUNT(*) AS n FROM orders"})
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["n"] > 0


def test_query_blocks_ddl(monkeypatch):
    _fresh_limiter(monkeypatch)
    resp = client.post("/api/query", json={"sql": "DROP TABLE orders"})
    assert resp.status_code == 400


def test_query_can_be_switched_off(monkeypatch):
    """线上把裸 SQL 口关掉：返回 404，不告诉调用方「这里只是被禁了」"""
    monkeypatch.setattr(settings, "enable_raw_sql", False)
    resp = client.post("/api/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 404


def test_ask_without_key_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    resp = client.post("/api/ask", json={"question": "上个月的营收"})
    assert resp.status_code == 503


def test_ask_end_to_end_with_fake_model(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test")
    _fresh_limiter(monkeypatch)
    monkeypatch.setattr(
        llm,
        "complete",
        lambda system, user: json.dumps(
            {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "", "chart": "metric"}
        ),
    )
    resp = client.post("/api/ask", json={"question": "一共有多少订单"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["session_id"]
    assert body["rows"][0]["n"] > 0


def test_session_endpoints_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test")
    _fresh_limiter(monkeypatch)
    monkeypatch.setattr(
        llm,
        "complete",
        lambda system, user: json.dumps(
            {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "", "chart": "metric"}
        ),
    )
    sid = client.post("/api/ask", json={"question": "一共有多少订单"}).json()["session_id"]

    turns = client.get(f"/api/session/{sid}").json()["turns"]
    assert len(turns) == 1

    assert client.delete(f"/api/session/{sid}").json()["cleared"] is True
    assert client.get(f"/api/session/{sid}").json()["turns"] == []
