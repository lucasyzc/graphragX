from app.core.config import get_settings


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_config_health_default_disabled(client, monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "none")
    monkeypatch.setenv("CHAT_API_BASE", "")
    monkeypatch.setenv("CHAT_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    monkeypatch.setenv("OPENAI_WIRE_API", "chat_completions")
    get_settings.cache_clear()
    resp = client.get("/health/chat-config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["provider"] in {"none", "disabled"}
    assert payload["enabled"] is False
    assert payload["configured"] is False
    assert payload["wire_api"] == "chat_completions"
    get_settings.cache_clear()


def test_chat_config_health_openai_compatible_ready(client, monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CHAT_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("CHAT_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_WIRE_API", "chat_completions")
    get_settings.cache_clear()

    resp = client.get("/health/chat-config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["provider"] == "openai_compatible"
    assert payload["enabled"] is True
    assert payload["configured"] is True
    assert payload["model"] == "gpt-4o-mini"
    assert payload["wire_api"] == "chat_completions"
    get_settings.cache_clear()


def test_chat_config_health_openai_alias_auto_enabled(client, monkeypatch):
    monkeypatch.delenv("CHAT_PROVIDER", raising=False)
    monkeypatch.setenv("CHAT_API_BASE", "")
    monkeypatch.setenv("CHAT_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://sub.jlypx.de")
    monkeypatch.setenv("OPENAI_WIRE_API", "responses")
    get_settings.cache_clear()

    resp = client.get("/health/chat-config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["provider"] == "openai_compatible"
    assert payload["enabled"] is True
    assert payload["configured"] is True
    assert payload["model"] == "gpt-4o-mini"
    assert payload["wire_api"] == "responses"
    get_settings.cache_clear()
