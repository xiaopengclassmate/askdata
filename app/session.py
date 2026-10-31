"""会话上下文：多轮追问（"那上个月呢""换个门店看看"）需要知道上一轮问的是什么。

只用内存字典 + 过期时间，不引 Redis——演示项目里加一个外部依赖，
换来的是部署复杂度，不是能力。真要上多实例，把这里换成 Redis 的 list 就行，
接口只有 append / history / clear 三个方法。
"""

from __future__ import annotations

import time
import uuid

MAX_TURNS = 5          # 每个会话只留最近 5 轮，够追问，也不会把 prompt 撑爆
TTL_SECONDS = 30 * 60  # 半小时不活动就丢，和人的使用节奏一致


class SessionStore:
    def __init__(self, max_turns: int = MAX_TURNS, ttl: int = TTL_SECONDS) -> None:
        self._data: dict[str, list[dict]] = {}
        self._touched: dict[str, float] = {}
        self.max_turns = max_turns
        self.ttl = ttl

    def _expire(self) -> None:
        now = time.time()
        for sid in [s for s, t in self._touched.items() if now - t > self.ttl]:
            self._data.pop(sid, None)
            self._touched.pop(sid, None)

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def append(self, session_id: str, question: str, sql: str = "", explanation: str = "") -> None:
        self._expire()
        turns = self._data.setdefault(session_id, [])
        turns.append({"question": question, "sql": sql, "explanation": explanation})
        del turns[:-self.max_turns]
        self._touched[session_id] = time.time()

    def history(self, session_id: str) -> list[dict]:
        self._expire()
        return list(self._data.get(session_id, []))

    def clear(self, session_id: str) -> None:
        self._data.pop(session_id, None)
        self._touched.pop(session_id, None)


store = SessionStore()


def render_history(turns: list[dict], keep: int = 2) -> str:
    """把上文渲染成给模型看的一小段，只带最近 keep 轮，且不带结果数据。"""
    recent = [t for t in turns[-keep:] if t.get("question")]
    if not recent:
        return ""
    lines = []
    for t in recent:
        lines.append(f"- 用户问：{t['question']}")
        if t.get("sql"):
            lines.append(f"  当时生成的 SQL：{' '.join(t['sql'].split())}")
    return "\n".join(lines)
