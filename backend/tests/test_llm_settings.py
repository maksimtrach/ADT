from __future__ import annotations

from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from backend.app.auth.security import hash_password
from backend.app.core.config import settings
from backend.app.core.llm_config import GenerationOptions, LLMConfiguration, LLMProfile, get_llm_configuration
from backend.app.db.database import connection, initialize_database, utc_now
from backend.app.main import app
from backend.app.services import llm_settings
from backend.app.services.llm_settings import probe_llm_profile, save_llm_configuration


def configuration(model: str = "qwen/test") -> LLMConfiguration:
    return LLMConfiguration(
        active_profile="local",
        fallback_profiles=["remote"],
        profiles={
            "local": LLMProfile(
                enabled=True,
                protocol="ollama",
                base_url="http://127.0.0.1:11434",
                model=model,
                no_think_suffix=True,
                generation=GenerationOptions(temperature=0, max_tokens=1200, context_size=8192),
            ),
            "remote": LLMProfile(
                enabled=True,
                protocol="openai",
                base_url="http://192.0.2.1:1234/v1",
                model="remote/model",
                api_key_env="REMOTE_TEST_KEY",
            ),
        },
    )


def add_admin() -> None:
    with connection() as db:
        db.execute(
            "INSERT INTO users (id, name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), "Admin", "admin@example.test", hash_password("TestPassword123!"), "ADMIN", utc_now()),
        )


def test_configuration_is_saved_atomically_and_cache_is_refreshed(tmp_path, monkeypatch):
    path = tmp_path / "llm.toml"
    monkeypatch.setattr(settings, "llm_config_path", path)
    monkeypatch.setattr(settings, "llm_profile", None)
    get_llm_configuration.cache_clear()

    first = save_llm_configuration(configuration())
    assert first.active_profile == "local"
    assert get_llm_configuration().profiles["local"].model == "qwen/test"

    save_llm_configuration(configuration("qwen/updated"))
    assert get_llm_configuration().profiles["local"].model == "qwen/updated"
    assert path.with_name("llm.toml.bak").is_file()
    assert "qwen/test" in path.with_name("llm.toml.bak").read_text(encoding="utf-8")
    get_llm_configuration.cache_clear()


def test_probe_reports_server_and_model_separately(monkeypatch):
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen/test"}, {"name": "another"}]})

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm_settings.httpx, "Client", client_factory)
    result = probe_llm_profile("local", configuration().profiles["local"])
    assert result.server_available is True
    assert result.model_available is True
    assert result.available is True
    assert result.models == ["another", "qwen/test"]


def test_llm_settings_api_is_admin_only_and_accepts_unsaved_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "llm_config_path", tmp_path / "llm.toml")
    monkeypatch.setattr(settings, "llm_profile", None)
    get_llm_configuration.cache_clear()
    save_llm_configuration(configuration())
    initialize_database()
    add_admin()

    expected = llm_settings.LLMProbeResult(
        profile_name="local",
        server_available=True,
        model_available=True,
        available=True,
        latency_ms=12,
        status_code=200,
        message="Модель доступна",
        models=["qwen/test"],
    )
    monkeypatch.setattr("backend.app.api.llm_settings.probe_llm_profile", lambda name, profile: expected)

    with TestClient(app) as client:
        assert client.get("/api/llm-settings").status_code == 401
        login = client.post("/api/auth/login", json={"email": "admin@example.test", "password": "TestPassword123!"})
        assert login.status_code == 200

        response = client.get("/api/llm-settings")
        assert response.status_code == 200
        assert response.json()["data"]["configuration"]["active_profile"] == "local"

        payload = {"profile_name": "local", "profile": configuration().profiles["local"].model_dump(mode="json")}
        checked = client.post("/api/llm-settings/test", json=payload)
        assert checked.status_code == 200
        assert checked.json()["data"]["available"] is True

        updated = configuration("qwen/new")
        saved = client.put("/api/llm-settings", json=updated.model_dump(mode="json"))
        assert saved.status_code == 200
        assert saved.json()["data"]["configuration"]["profiles"]["local"]["model"] == "qwen/new"

    get_llm_configuration.cache_clear()
