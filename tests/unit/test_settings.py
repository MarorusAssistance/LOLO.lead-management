from __future__ import annotations

import os

from lolo_lead_management.config.env import load_env_file, parse_env_file
from lolo_lead_management.config.settings import get_settings

from tests.helpers import workspace_tmp_dir


LOLO_ENV_KEYS = [
    "LOLO_APP_NAME",
    "LOLO_ENVIRONMENT",
    "LOLO_DATABASE_PATH",
    "LOLO_TAVILY_API_KEY",
    "TAVILY_API_KEY",
    "LOLO_TAVILY_BASE_URL",
    "TAVILY_BASE_URL",
    "LOLO_LM_STUDIO_BASE_URL",
    "LOLO_LM_STUDIO_MODEL",
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
