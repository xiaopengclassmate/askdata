"""大模型调用。刻意不引入 LangChain：

整条链路只有「拼 prompt → 调接口 → 解析 JSON」三步，用 httpx 直接打
OpenAI 兼容接口，代码不到 50 行。上框架会让每一步都藏起来，
面试被问「prompt 具体怎么拼的」时只能指向框架文档。
"""

from __future__ import annotations

import json
import re

import httpx

from .config import settings


class LLMError(Exception):
    pass


def complete(system: str, user: str, temperature: float = 0.0) -> str:
    if not settings.llm_enabled:
        raise LLMError("未配置 DASHSCOPE_API_KEY，先按 .env.example 建 .env")

    payload = {
        "model": settings.dashscope_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.dashscope_base_url.rstrip('/')}/chat/completions"

    last: Exception | None = None
    for _ in range(2):  # 网络抖动重试一次，业务失败不重试
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise LLMError(f"模型接口 {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, LLMError) as exc:
            last = exc
    raise LLMError(str(last))


_JSON_RE = re.compile(r"\{.*\}", re.S)
_FENCE_RE = re.compile(r"```(?:json|sql)?\s*(.*?)```", re.S)


def parse_sql_json(text: str) -> dict:
    """模型偶尔会在 JSON 外面加解释文字，这里宽松地抠出对象"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = _FENCE_RE.search(text)
    if fence:
        inner = fence.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            return {"sql": inner}

    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"sql": text}
