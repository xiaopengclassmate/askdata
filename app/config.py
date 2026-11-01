"""运行时配置，全部来自环境变量 / .env（.env 不进版本库）"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "db" / "askdata.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE / ".env", extra="ignore")

    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-plus"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    query_max_rows: int = 200
    query_timeout_seconds: float = 5.0

    # ---- 公网保护（0 表示关闭该项）----
    # 单 IP 每分钟：/api/ask 会真实调模型，卡得紧一点；/api/query 只执行本地 SQL。
    ask_rate_per_min: int = 10
    query_rate_per_min: int = 30
    # 全局每日 ask 次数上限：挡住「换 IP 刷」这类打法，把最坏损失锁死。
    # 一次 ask 最多触发 3 次模型调用（报错自修复重试），这里按 ask 次数算。
    daily_ask_quota: int = 200
    # 同时处理的 ask 请求数：单次 2~3 秒，小内存机器扛不住几十个并发。
    max_concurrent_ask: int = 4
    # 前面挂了可信代理（Nginx / SLB）才开；直连时开了等于允许伪造 IP 绕过限流。
    trust_proxy_headers: bool = False

    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.dashscope_api_key)


settings = Settings()
