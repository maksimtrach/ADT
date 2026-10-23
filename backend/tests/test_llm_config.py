from __future__ import annotations

from backend.app.core.config import settings
from backend.app.core.llm_config import LLMProfile, read_llm_configuration
from backend.app.services.llm.openai_compatible import OpenAICompatibleAdapter


def test_reads_profiles_and_supports_environment_selection(tmp_path, monkeypatch):
    config_path = tmp_path / "llm.toml"
    config_path.write_text(
        """
active_profile = "local"
fallback_profiles = ["remote"]

[profiles.local]
protocol = "ollama"
base_url = "http://127.0.0.1:11434/"
model = "local-model"

[profiles.remote]
protocol = "openai"
base_url = "http://192.168.1.65:1234/v1/"
model = "remote-model"
api_key_env = "REMOTE_LLM_API_KEY"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "llm_profile", None)

    configuration = read_llm_configuration(config_path)

    assert configuration.profile_order() == ["local", "remote"]
    assert configuration.profiles["local"].base_url == "http://127.0.0.1:11434"
    assert configuration.profiles["remote"].api_key_env == "REMOTE_LLM_API_KEY"

    monkeypatch.setattr(settings, "llm_profile", "remote")
    assert configuration.profile_order() == ["remote"]


def test_api_key_prefers_environment_and_can_be_read_from_dotenv(monkeypatch):
    adapter = OpenAICompatibleAdapter(
        "remote",
        LLMProfile(
            protocol="openai",
            base_url="https://llm.example.test/v1",
            model="model",
            api_key_env="REMOTE_LLM_API_KEY",
        ),
    )
    monkeypatch.delenv("REMOTE_LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        "backend.app.services.llm.base.dotenv_values",
        lambda path: {"REMOTE_LLM_API_KEY": "dotenv-secret"},
    )
    assert adapter.headers()["Authorization"] == "Bearer dotenv-secret"

    monkeypatch.setenv("REMOTE_LLM_API_KEY", "environment-secret")
    assert adapter.headers()["Authorization"] == "Bearer environment-secret"
