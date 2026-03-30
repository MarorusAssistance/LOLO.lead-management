from __future__ import annotations

from functools import lru_cache
from os import environ
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .env import load_env_file


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ENV_FIELD_MAP: ClassVar[dict[str, str]] = {
        "LOLO_APP_NAME": "app_name",
        "LOLO_ENVIRONMENT": "environment",
        "LOLO_DATABASE_PATH": "database_path",
        "LOLO_TAVILY_API_KEY": "tavily_api_key",
        "TAVILY_API_KEY": "tavily_api_key",
        "LOLO_TAVILY_BASE_URL": "tavily_base_url",
        "TAVILY_BASE_URL": "tavily_base_url",
        "LOLO_LM_STUDIO_BASE_URL": "lm_studio_base_url",
        "LOLO_LM_STUDIO_MODEL": "lm_studio_model",
        "LOLO_LLM_ENABLED": "llm_enabled",
        "LOLO_SEARCH_ENABLED": "search_enabled",
        "LOLO_SEARCH_MAX_RESULTS": "search_max_results",
        "LOLO_SOURCE_ATTEMPT_BUDGET": "source_attempt_budget",
        "LOLO_ENRICH_ATTEMPT_BUDGET": "enrich_attempt_budget",
        "LOLO_SHORTLIST_SIZE": "shortlist_size",
        "LOLO_QUERY_HISTORY_WINDOW_DAYS": "query_history_window_days",
        "LOLO_EXECUTION_RESULTS_DIR": "execution_results_dir",
    }

    app_name: str = "LOLO Lead Management"
    environment: str = "development"
    database_path: str = "data/lead_management.sqlite3"
    tavily_api_key: str | None = None
    tavily_base_url: str = "https://api.tavily.com/search"
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1/chat/completions"
    lm_studio_model: str = "qwen/qwen3-30b-a3b-instruct-2507"
    llm_enabled: bool = False
    search_enabled: bool = False
    search_max_results: int = Field(default=5, ge=1, le=10)
    source_attempt_budget: int = Field(default=6, ge=1, le=20)
    enrich_attempt_budget: int = Field(default=1, ge=0, le=5)
    shortlist_size: int = Field(default=5, ge=1, le=10)
    query_history_window_days: int = Field(default=30, ge=1, le=365)
    execution_results_dir: str = "data/execution-results"

    @property
    def database_file(self) -> Path:
        return Path(self.database_path)

    @classmethod
    def from_environ(cls, env: dict[str, str] | None = None) -> "Settings":
        source = env if env is not None else dict(environ)
        payload = {
            field_name: source[env_name]
            for env_name, field_name in cls.ENV_FIELD_MAP.items()
            if env_name in source and source[env_name] != ""
        }
        return cls.model_validate(payload)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env_file()
    return Settings.from_environ()
