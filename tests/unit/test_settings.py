from __future__ import annotations

import os

from lolo_lead_management.config.env import load_env_file, parse_env_file
from lolo_lead_management.config.settings import Settings, get_settings

from tests.helpers import workspace_tmp_dir


LOLO_ENV_KEYS = [
    "LOLO_APP_NAME",
    "LOLO_ENVIRONMENT",
    "LOLO_DATABASE_PATH",
    "LOLO_TAVILY_API_KEY",
    "TAVILY_API_KEY",
    "LOLO_TAVILY_BASE_URL",
    "TAVILY_BASE_URL",
    "LOLO_LLM_BASE_URL",
    "LOLO_LLM_MODEL",
    "LOLO_LLM_API_KEY",
    "LOLO_LLM_MAX_COMPLETION_TOKENS",
    "LOLO_LLM_REASONING_EFFORT",
    "OPENAI_API_KEY",
    "LOLO_LM_STUDIO_BASE_URL",
    "LOLO_LM_STUDIO_MODEL",
    "LOLO_LLM_TIMEOUT_SECONDS",
    "LOLO_RERANKER_ENABLED",
    "LOLO_RERANKER_MODEL_KEY",
    "LOLO_RERANKER_MODEL_PATH",
    "LOLO_RERANKER_LM_STUDIO_BASE_URL",
    "LOLO_RERANKER_ENGINE_BASE_URL",
    "LOLO_RERANKER_BOOTSTRAP_ENABLED",
    "LOLO_RERANKER_RUNTIME_CACHE_DIR",
    "LOLO_RERANKER_TIMEOUT_SECONDS",
    "LOLO_RERANKER_TOP_K_INITIAL",
    "LOLO_RERANKER_EXPANSION_DOCS",
    "LOLO_LLM_ENABLED",
    "LOLO_SEARCH_ENABLED",
    "LOLO_SEARCH_MAX_RESULTS",
    "LOLO_SOURCE_ATTEMPT_BUDGET",
    "LOLO_ENRICH_ATTEMPT_BUDGET",
    "LOLO_SHORTLIST_SIZE",
    "LOLO_QUERY_HISTORY_WINDOW_DAYS",
    "LOLO_EXECUTION_RESULTS_DIR",
]


def restore_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_parse_env_file_supports_quotes_and_comments() -> None:
    tmp_path = workspace_tmp_dir("settings-parse")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'LOLO_APP_NAME="LOLO Test"\n'
        "LOLO_SEARCH_ENABLED=true\n"
        "LOLO_DATABASE_PATH=data/custom.sqlite3 # comment\n",
        encoding="utf-8",
    )

    parsed = parse_env_file(env_file)

    assert parsed["LOLO_APP_NAME"] == "LOLO Test"
    assert parsed["LOLO_SEARCH_ENABLED"] == "true"
    assert parsed["LOLO_DATABASE_PATH"] == "data/custom.sqlite3"


def test_get_settings_auto_loads_dotenv_from_cwd(monkeypatch) -> None:
    snapshot = {key: os.environ.get(key) for key in LOLO_ENV_KEYS}
    try:
        for key in LOLO_ENV_KEYS:
            os.environ.pop(key, None)

        tmp_path = workspace_tmp_dir("settings-autoload")
        env_file = tmp_path / ".env"
        expected_env_path = env_file.resolve()
        env_file.write_text(
            "LOLO_APP_NAME=LOLO From Env\n"
            "LOLO_DATABASE_PATH=data/from-dotenv.sqlite3\n"
            "LOLO_SEARCH_ENABLED=true\n"
            "TAVILY_API_KEY=tvly-test-key\n"
            "LOLO_LLM_ENABLED=true\n"
            "LOLO_SOURCE_ATTEMPT_BUDGET=9\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        get_settings.cache_clear()

        settings = get_settings()

        assert settings.app_name == "LOLO From Env"
        assert settings.database_path == "data/from-dotenv.sqlite3"
        assert settings.search_enabled is True
        assert settings.tavily_api_key == "tvly-test-key"
        assert settings.llm_enabled is True
        assert settings.source_attempt_budget == 9
        assert load_env_file() == expected_env_path
    finally:
        get_settings.cache_clear()
        restore_env(snapshot)


def test_settings_selects_development_database_by_default() -> None:
    parsed = Settings.from_environ({"LOLO_ENVIRONMENT": "development"})
    assert parsed.database_path == "data/lead_management.development.sqlite3"


def test_settings_selects_production_database_by_default() -> None:
    parsed = Settings.from_environ({"LOLO_ENVIRONMENT": "production"})
    assert parsed.database_path == "data/lead_management.production.sqlite3"


def test_settings_database_override_wins_over_environment_default() -> None:
    parsed = Settings.from_environ(
        {
            "LOLO_ENVIRONMENT": "development",
            "LOLO_DATABASE_PATH": "data/custom.sqlite3",
        }
    )
    assert parsed.database_path == "data/custom.sqlite3"


def test_settings_support_generic_llm_env_names() -> None:
    parsed = Settings.from_environ(
        {
            "LOLO_LLM_BASE_URL": "https://api.openai.com/v1/chat/completions",
            "LOLO_LLM_MODEL": "gpt-5-mini",
            "LOLO_LLM_API_KEY": "sk-test",
            "LOLO_LLM_MAX_COMPLETION_TOKENS": "4096",
            "LOLO_LLM_REASONING_EFFORT": "medium",
        }
    )
    assert parsed.llm_base_url == "https://api.openai.com/v1/chat/completions"
    assert parsed.llm_model == "gpt-5-mini"
    assert parsed.llm_api_key == "sk-test"
    assert parsed.llm_max_completion_tokens == 4096
    assert parsed.llm_reasoning_effort == "medium"


def test_settings_keep_backward_compatible_llm_aliases() -> None:
    parsed = Settings.from_environ(
        {
            "LOLO_LM_STUDIO_BASE_URL": "http://127.0.0.1:1234/v1/chat/completions",
            "LOLO_LM_STUDIO_MODEL": "qwen/test-model",
            "OPENAI_API_KEY": "sk-legacy",
        }
    )
    assert parsed.llm_base_url == "http://127.0.0.1:1234/v1/chat/completions"
    assert parsed.llm_model == "qwen/test-model"
    assert parsed.llm_api_key == "sk-legacy"


def test_settings_loads_reranker_configuration() -> None:
    parsed = Settings.from_environ(
        {
            "LOLO_RERANKER_ENABLED": "true",
            "LOLO_RERANKER_MODEL_KEY": "text-embedding-bge-reranker-v2-m3",
            "LOLO_RERANKER_MODEL_PATH": "C:/models/bge-reranker-v2-m3-Q8_0.gguf",
            "LOLO_RERANKER_LM_STUDIO_BASE_URL": "http://127.0.0.1:1234",
            "LOLO_RERANKER_ENGINE_BASE_URL": "http://127.0.0.1:8081",
            "LOLO_RERANKER_BOOTSTRAP_ENABLED": "true",
            "LOLO_RERANKER_RUNTIME_CACHE_DIR": "data/runtime/reranker",
            "LOLO_RERANKER_TIMEOUT_SECONDS": "77",
            "LOLO_RERANKER_TOP_K_INITIAL": "10",
            "LOLO_RERANKER_EXPANSION_DOCS": "2",
        }
    )

    assert parsed.reranker_enabled is True
    assert parsed.reranker_model_key == "text-embedding-bge-reranker-v2-m3"
    assert parsed.reranker_model_path == "C:/models/bge-reranker-v2-m3-Q8_0.gguf"
    assert parsed.reranker_lm_studio_base_url == "http://127.0.0.1:1234"
    assert parsed.reranker_engine_base_url == "http://127.0.0.1:8081"
    assert parsed.reranker_bootstrap_enabled is True
    assert parsed.reranker_runtime_cache_dir == "data/runtime/reranker"
    assert parsed.reranker_timeout_seconds == 77
    assert parsed.reranker_top_k_initial == 10
    assert parsed.reranker_expansion_docs == 2
