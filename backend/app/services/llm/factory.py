from __future__ import annotations

from backend.app.core.llm_config import LLMProfile
from backend.app.services.llm.base import LLMAdapter
from backend.app.services.llm.ollama import OllamaAdapter
from backend.app.services.llm.openai_compatible import OpenAICompatibleAdapter


def build_adapter(profile_name: str, profile: LLMProfile) -> LLMAdapter:
    if profile.protocol == "ollama":
        return OllamaAdapter(profile_name, profile)
    if profile.protocol == "openai":
        return OpenAICompatibleAdapter(profile_name, profile)
    raise ValueError(f"Неподдерживаемый LLM-протокол: {profile.protocol}")


__all__ = ["build_adapter"]
