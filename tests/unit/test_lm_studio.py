import json

from lolo_lead_management.adapters.llm.lm_studio import LmStudioLlmPort
from lolo_lead_management.config.settings import Settings


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode("utf-8")


def test_settings_loads_llm_timeout_seconds_from_env() -> None:
    settings = Settings.from_environ({"LOLO_LLM_TIMEOUT_SECONDS": "123"})

    assert settings.llm_timeout_seconds == 123


def test_lm_studio_uses_configured_timeout_and_does_not_duplicate_schema(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr("lolo_lead_management.adapters.llm.lm_studio.request.urlopen", fake_urlopen)
    port = LmStudioLlmPort(base_url="http://localhost", model="test-model", timeout_seconds=42)

    result = port.generate_json(
        agent_name="AssemblerAgent",
        system_prompt="system",
        input_payload={"foo": "bar"},
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    )

    assert result == {"ok": True}
    assert captured["timeout"] == 42
    user_content = json.loads(captured["payload"]["messages"][1]["content"])
    assert "schema" not in user_content
    assert captured["payload"]["response_format"]["json_schema"]["schema"]["type"] == "object"
