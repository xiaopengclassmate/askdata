"""限流 / 日配额 / 并发闸门：纯内存状态，注入假时钟就能把时间跑完"""

import pytest
from starlette.requests import Request

from app.ratelimit import ConcurrencyGate, LimitHit, RateLimiter, client_ip
from app.config import settings


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ask",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("203.0.113.9", 51234),
        }
    )


def test_allows_up_to_limit_then_blocks():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(3):
        limiter.hit("1.1.1.1", "ask", 3)
    with pytest.raises(LimitHit) as exc:
        limiter.hit("1.1.1.1", "ask", 3)
    assert 1 <= exc.value.retry_after <= 60


def test_window_slides_and_recovers():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("1.1.1.1", "ask", 3)
    clock.advance(61)  # 窗口整段过期
    limiter.hit("1.1.1.1", "ask", 3)  # 不该抛


def test_limits_are_per_ip_and_per_scope():
    limiter = RateLimiter(clock=FakeClock())
    limiter.hit("1.1.1.1", "ask", 1)
    limiter.hit("2.2.2.2", "ask", 1)  # 别的 IP 不受影响
    limiter.hit("1.1.1.1", "query", 1)  # 别的接口单独计数


def test_zero_limit_means_disabled():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(100):
        limiter.hit("1.1.1.1", "ask", 0)


def test_daily_quota_blocks_after_quota():
    day = {"value": "2026-11-01"}
    limiter = RateLimiter(clock=FakeClock(), today=lambda: day["value"])
    assert limiter.consume_daily("ask", 2) == 1
    assert limiter.consume_daily("ask", 2) == 2
    with pytest.raises(LimitHit):
        limiter.consume_daily("ask", 2)
    assert limiter.remaining_daily("ask", 2) == 0
    # 换一天自动清零
    day["value"] = "2026-11-02"
    assert limiter.consume_daily("ask", 2) == 1


def test_daily_quota_zero_means_unlimited():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(50):
        limiter.consume_daily("ask", 0)
    assert limiter.remaining_daily("ask", 0) == -1


def test_concurrency_gate_rejects_instead_of_queueing():
    gate = ConcurrencyGate(1)
    with gate:
        with pytest.raises(LimitHit):
            with gate:
                pass
    with gate:  # 释放后能再进
        pass


def test_client_ip_ignores_forged_header_by_default(monkeypatch):
    """默认不信 X-Forwarded-For：那个头能伪造，信了等于给限流开后门"""
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    req = make_request({"X-Forwarded-For": "9.9.9.9"})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_uses_forwarded_when_proxy_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    req = make_request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert client_ip(req) == "9.9.9.9"


def test_query_endpoint_returns_429_with_retry_after(monkeypatch):
    """接口层验证：超频真的落到 429，且带 Retry-After（客户端才知道等多久）"""
    from fastapi.testclient import TestClient

    from app import main, ratelimit

    monkeypatch.setattr(ratelimit, "limiter", RateLimiter(clock=FakeClock()))
    monkeypatch.setattr(settings, "query_rate_per_min", 2)
    client = TestClient(main.app)
    assert client.post("/api/query", json={"sql": "SELECT 1"}).status_code == 200
    assert client.post("/api/query", json={"sql": "SELECT 1"}).status_code == 200
    resp = client.post("/api/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"].isdigit()

