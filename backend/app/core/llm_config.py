from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.core.config import settings


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int = Field(default=1800, ge=1)
    context_size: int | None = Field(default=None, ge=512)


class LLMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    protocol: Literal["ollama", "openai"]
    base_url: str
    model: str
    timeout_seconds: int = Field(default=180, ge=1)
    thinking: bool = False
    no_think_suffix: bool = False
    keep_alive: str | None = None
    api_key_env: str | None = None
    structured_output: bool = True
    generation: GenerationOptions = Field(default_factory=GenerationOptions)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url должен начинаться с http:// или https://")
        return normalized

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model не может быть пустым")
        return normalized

    @field_validator("api_key_env", "keep_alive", mode="before")
    @classmethod
    def empty_string_as_none(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            value = value.strip()
        return value or None


class LLMConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_profile: str
    fallback_profiles: list[str] = Field(default_factory=list)
    profiles: dict[str, LLMProfile]

    @model_validator(mode="after")
    def validate_profile_references(self) -> "LLMConfiguration":
        if self.active_profile not in self.profiles:
            raise ValueError(f"Не найден активный LLM-профиль: {self.active_profile}")
        unknown = [name for name in self.fallback_profiles if name not in self.profiles]
        if unknown:
            raise ValueError(f"Не найдены резервные LLM-профили: {', '.join(unknown)}")
        if self.active_profile in self.fallback_profiles:
            raise ValueError("Активный LLM-профиль не должен повторяться в fallback_profiles")
        if len(self.fallback_profiles) != len(set(self.fallback_profiles)):
            raise ValueError("fallback_profiles содержит повторяющиеся профили")
        return self

    @property
    def selected_profile(self) -> str:
        return settings.llm_profile or self.active_profile

    def profile_order(self) -> list[str]:
        selected = self.selected_profile
        if selected not in self.profiles:
            raise ValueError(f"Не найден выбранный LLM-профиль: {selected}")
        return [selected, *[name for name in self.fallback_profiles if name != selected]]


def _legacy_configuration() -> LLMConfiguration:
    return LLMConfiguration(
        active_profile="legacy",
        profiles={
            "legacy": LLMProfile(
                enabled=settings.llm_provider.lower() == "ollama",
                protocol="ollama",
                base_url=settings.ollama_url,
                model=settings.ollama_model,
                timeout_seconds=settings.llm_timeout_seconds,
                thinking=False,
                no_think_suffix=True,
                keep_alive="10m",
                generation=GenerationOptions(temperature=0, max_tokens=1800, context_size=16384),
            )
        },
    )


def read_llm_configuration(path: Path) -> LLMConfiguration:
    with path.open("rb") as source:
        data = tomllib.load(source)
    configuration = LLMConfiguration.model_validate(data)
    if settings.llm_profile and settings.llm_profile not in configuration.profiles:
        raise ValueError(f"LLM_PROFILE ссылается на неизвестный профиль: {settings.llm_profile}")
    return configuration


@lru_cache
def get_llm_configuration() -> LLMConfiguration:
    path = settings.llm_config_path
    if not path.is_file():
        return _legacy_configuration()
    return read_llm_configuration(path)


__all__ = [
    "GenerationOptions",
    "LLMConfiguration",
    "LLMProfile",
    "get_llm_configuration",
    "read_llm_configuration",
]
