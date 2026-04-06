from fastapi import APIRouter

from app.core.config import get_settings
from app.services.runtime_check import collect_dependency_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/dependencies")
def dependency_health() -> dict:
    dependencies = collect_dependency_status()
    all_ok = all((item["ok"] or not item["required"]) for item in dependencies.values())
    return {"status": "ok" if all_ok else "degraded", "dependencies": dependencies}


@router.get("/health/chat-config")
def chat_config_health() -> dict:
    settings = get_settings()
    provider = settings.resolved_chat_provider()
    wire_api = settings.resolved_openai_wire_api()
    enabled = provider not in {"none", "disabled"}

    if not enabled:
        return {
            "provider": provider,
            "enabled": False,
            "configured": False,
            "model": None,
            "wire_api": wire_api,
            "note": "chat provider disabled",
        }

    if provider == "openai_compatible":
        configured = bool(settings.resolved_chat_api_base())
        return {
            "provider": provider,
            "enabled": True,
            "configured": configured,
            "model": settings.resolved_chat_model() if configured else None,
            "wire_api": wire_api,
            "note": "ready" if configured else "missing CHAT_API_BASE/OPENAI_BASE_URL",
        }

    return {
        "provider": provider,
        "enabled": True,
        "configured": True,
        "model": settings.resolved_chat_model(),
        "wire_api": wire_api,
        "note": "custom provider",
    }
