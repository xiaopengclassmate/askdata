"""公网接口的成本保护：按 IP 限流 + 全局日配额 + 并发闸门。

这个服务把 DashScope 的 key 放在公网后面，不加保护等于把额度送给别人刷。
三条线互相独立，各自挡一种打法：

  · 单 IP 每分钟次数  —— 挡同一个 IP 的脚本猛刷（最便宜的一层）
  · 全局每日次数      —— 挡换着 IP 刷：额度用完就停，损失有上界
  · 并发数            —— 挡同时打满：单次 LLM 2~3 秒，1.7G 内存的机器
                         撑不住几十个并发（内存抖动会连带把 sshd 打挂）

三者都是**进程内**状态，因为线上是单容器单进程（uvicorn 默认 1 worker）。
换成多 worker / 多实例时这套得挪到 Redis（INCR + EXPIRE，或滑动窗口用 ZSET），
现在这个体量不值得为此多部署一个组件——但要知道边界在哪。
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request

from .config import settings


class LimitHit(Exception):
    """触发限流。retry_after 是建议客户端等多久再试（秒）。"""

    def __init__(self, reason: str, retry_after: int = 60) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class RateLimiter:
    """滑动窗口计数 + 按天配额。

    窗口用单调时钟，不受系统改时间影响；桶里只留窗口内的打点，
    所以内存正比于「活跃 IP 数 × 窗口内请求数」，不会无限涨。
    """

    def __init__(
        self,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] | None = None,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._daily: dict[str, int] = {}

    def hit(self, ip: str, scope: str, limit_per_window: int) -> None:
        """记一次调用。超限抛 LimitHit。limit_per_window <= 0 表示不限制。"""
        if limit_per_window <= 0:
            return
        now = self._clock()
        key = (ip, scope)
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit_per_window:
                retry = max(1, math.ceil(bucket[0] + self._window - now))
                raise LimitHit(
                    f"请求太频繁：{scope} 每 {int(self._window)} 秒最多 "
                    f"{limit_per_window} 次，请 {retry} 秒后再试",
                    retry,
                )
            bucket.append(now)
            # 顺手清掉窗口内没打点的空桶，防止被大量一次性 IP 撑爆
            if len(self._hits) > 1024:
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]

    def consume_daily(self, scope: str, quota: int) -> int:
        """占掉今天的一个名额，返回今天已用次数。quota <= 0 表示不限。"""
        if quota <= 0:
            return 0
        day = self._today()
        key = f"{day}:{scope}"
        with self._lock:
            used = self._daily.get(key, 0)
            if used >= quota:
                raise LimitHit(f"今日演示额度（{quota} 次）已用完，明天再来", 3600)
            self._daily[key] = used + 1
            # 只保留当天的计数，跨天自动清零
            for k in [k for k in self._daily if not k.startswith(day)]:
                del self._daily[k]
            return used + 1

    def remaining_daily(self, scope: str, quota: int) -> int:
        """今天还剩多少（给 /health 和日志用，不占名额）。"""
        if quota <= 0:
            return -1  # 不限制
        with self._lock:
            return max(0, quota - self._daily.get(f"{self._today()}:{scope}", 0))


class ConcurrencyGate:
    """非阻塞信号量：满了立刻拒绝，不排队。

    排队会让请求堆在内存里等，越堆越多；直接 429 让客户端重试更诚实。
    """

    def __init__(self, size: int) -> None:
        self.size = size
        self._sem = threading.Semaphore(size) if size > 0 else None

    def __enter__(self) -> "ConcurrencyGate":
        if self._sem is not None and not self._sem.acquire(blocking=False):
            raise LimitHit(
                f"当前同时处理的请求已达上限（{self.size} 个），请稍后重试", 3
            )
        return self

    def __exit__(self, *exc) -> bool:
        if self._sem is not None:
            self._sem.release()
        return False


def client_ip(request: Request) -> str:
    """取客户端 IP。

    默认只看 TCP 层的 request.client.host——**不看 X-Forwarded-For**，因为
    那个头谁都能伪造，信了就等于给限流开了后门（换个头绕过）。
    前面真挂了 Nginx 这类可信代理时，用 TRUST_PROXY_HEADERS=true 显式打开。
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# 进程级单例（配置来自环境变量，改配置要重启）
limiter = RateLimiter()
ask_gate = ConcurrencyGate(settings.max_concurrent_ask)
