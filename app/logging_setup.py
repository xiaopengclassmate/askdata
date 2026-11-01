"""统一日志：一行一条 `key=value`，docker logs 直接看得懂，不用接 ELK。

为什么不直接 logging.basicConfig：
  uvicorn 启动时会接管根 logger（自己的 handler + formatter），basicConfig
  加的 handler 大概率被覆盖或重复输出。所以这里给 `askdata` 命名 logger
  单独挂 handler，并关掉 propagate——输出稳定，也不和 uvicorn 的访问日志打架。
"""

from __future__ import annotations

import logging
import sys
import time

_configured = False


class _UtcFormatter(logging.Formatter):
    """时间统一打 UTC 并带 Z 后缀。

    容器默认就是 UTC，本地开发在 Windows 上是北京时间——同一个格式两种时区，
    看日志时最容易搞错。写死 UTC 并标出来，两边读数一致，也省得为了本地时间
    在镜像里装 tzdata。
    """

    converter = time.gmtime

    def formatTime(self, record, datefmt=None):  # noqa: N802 (沿用 logging 的命名)
        return super().formatTime(record, datefmt) + "Z"


def setup_logging(level: str = "INFO") -> logging.Logger:
    global _configured
    logger = logging.getLogger("askdata")
    if _configured:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _UtcFormatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    _configured = True
    return logger


def one_line(text: object, limit: int = 300) -> str:
    """把可能带换行/引号的文本压成一行，方便日志按 `key="value"` 解析。"""
    s = " ".join(str(text).split())
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s.replace('"', "'")
