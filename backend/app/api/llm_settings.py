from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.api.dependencies import admin_user
from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile, get_llm_configuration
from backend.app.db.database import record_audit
from backend.app.models.domain import UserPublic
from backend.app.services.llm_settings import probe_llm_profile, save_llm_configuration


router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])


class ProfileProbeInput(BaseModel):
    profile_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    profile: LLMProfile


def _settings_payload(configuration: LLMConfiguration) -> dict[str, object]:
    return {
        "configuration": configuration.model_dump(mode="json"),
        "selected_profile": configuration.selected_profile,
        "environment_override": settings.llm_profile,
    }


@router.get("")
def get_settings(_: UserPublic = Depends(admin_user)):
    return {"data": _settings_payload(get_llm_configuration())}


@router.put("")
def update_settings(configuration: LLMConfiguration, user: UserPublic = Depends(admin_user)):
    try:
        saved = save_llm_configuration(configuration)
    except (OSError, ValueError) as error:
        raise AppError(422, f"Не удалось сохранить настройки LLM: {error}", "INVALID_LLM_CONFIGURATION") from error
    record_audit(
        "LLM_SETTINGS_UPDATED",
        user_id=user.id,
        entity_type="LLM_CONFIGURATION",
        details={"activeProfile": saved.active_profile, "profileCount": len(saved.profiles)},
    )
    return {"data": _settings_payload(saved)}


@router.post("/test")
def test_profile(payload: ProfileProbeInput, user: UserPublic = Depends(admin_user)):
    result = probe_llm_profile(payload.profile_name, payload.profile)
    record_audit(
        "LLM_PROFILE_CHECKED",
        user_id=user.id,
        entity_type="LLM_PROFILE",
        entity_id=payload.profile_name,
        outcome="SUCCESS" if result.available else "FAILURE",
        details={
            "serverAvailable": result.server_available,
            "modelAvailable": result.model_available,
            "latencyMs": result.latency_ms,
        },
    )
    return {"data": result.model_dump(mode="json")}
