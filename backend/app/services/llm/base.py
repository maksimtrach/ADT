from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

import httpx
from dotenv import dotenv_values

from backend.app.core.llm_config import LLMProfile

TraceCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class LLMCallResult:
    content: str
    response_payload: dict[str, Any]
    duration_ms: int


class LLMAdapter(ABC):
    def __init__(self, profile_name: str, profile: LLMProfile) -> None:
        self.profile_name = profile_name
        self.profile = profile

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "profile": self.profile_name,
            "protocol": self.profile.protocol,
            "base_url": self.profile.base_url,
            "model": self.profile.model,
        }

    def api_key(self) -> str | None:
        if not self.profile.api_key_env:
            return None
        value = os.getenv(self.profile.api_key_env)
        if value:
            return value
        dotenv_value = dotenv_values(".env").get(self.profile.api_key_env)
        return dotenv_value if isinstance(dotenv_value, str) and dotenv_value else None

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.profile.api_key_env:
            token = self.api_key()
            if not token:
                raise RuntimeError(f"Не задана переменная окружения {self.profile.api_key_env}")
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def credentials_available(self) -> bool:
        return not self.profile.api_key_env or bool(self.api_key())

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def build_request(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
    ) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def extract_content(self, response_payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
        trace: TraceCallback | None = None,
    ) -> LLMCallResult:
        endpoint, request_payload = self.build_request(messages, schema, schema_name)
        if trace:
            trace("LLM_REQUEST", {**self.metadata, "endpoint": endpoint, "payload": request_payload})
        started_at = perf_counter()
        try:
            with httpx.Client(
                timeout=self.profile.timeout_seconds,
                trust_env=False,
                headers=self.headers(),
            ) as client:
                response = client.post(endpoint, json=request_payload)
                response_payload = response.json()
                traced_payload = dict(response_payload) if isinstance(response_payload, dict) else {"value": response_payload}
                traced_payload.pop("context", None)
                duration_ms = round((perf_counter() - started_at) * 1000)
                if trace:
                    trace(
                        "LLM_RESPONSE",
                        {
                            **self.metadata,
                            "status_code": response.status_code,
                            "duration_ms": duration_ms,
                            "payload": traced_payload,
                        },
                    )
                response.raise_for_status()
                if not isinstance(response_payload, dict):
                    raise ValueError("LLM вернула ответ не в формате JSON-объекта")
                content = self.extract_content(response_payload)
                if not content.strip():
                    raise ValueError("LLM вернула пустой content")
                return LLMCallResult(
                    content=content,
                    response_payload=response_payload,
                    duration_ms=duration_ms,
                )
        except Exception as error:
            if trace:
                trace(
                    "LLM_FAILED",
                    {
                        **self.metadata,
                        "error_type": type(error).__name__,
                        "message": str(error)[:1000],
                        "duration_ms": round((perf_counter() - started_at) * 1000),
                    },
                )
            raise
