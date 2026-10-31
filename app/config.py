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

    @property
    def llm_enabled(self) -> bool:
        return bool(self.dashscope_api_key)


settings = Settings()
