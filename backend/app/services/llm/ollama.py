from __future__ import annotations

from typing import Any

import httpx

from backend.app.services.llm.base import LLMAdapter


class OllamaAdapter(LLMAdapter):
    def available(self) -> bool:
        if not self.profile.enabled or not self.credentials_available():
            return False
        try:
            with httpx.Client(timeout=5, trust_env=False, headers=self.headers()) as client:
                response = client.get(f"{self.profile.base_url}/api/tags")
                response.raise_for_status()
            names = {item.get("name") for item in response.json().get("models", [])}
            return self.profile.model in names
        except Exception:
            return False

    def build_request(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
    ) -> tuple[str, dict[str, Any]]:
        del schema_name
        options: dict[str, Any] = {
            "temperature": self.profile.generation.temperature,
            "num_predict": self.profile.generation.max_tokens,
        }
        if self.profile.generation.context_size:
            options["num_ctx"] = self.profile.generation.context_size
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "stream": False,
            "think": self.profile.thinking,
            "format": schema,
            "options": options,
            "messages": messages,
        }
        if self.profile.keep_alive:
            payload["keep_alive"] = self.profile.keep_alive
        return f"{self.profile.base_url}/api/chat", payload

    def extract_content(self, response_payload: dict[str, Any]) -> str:
        content = response_payload["message"].get("content")
        return content if isinstance(content, str) else ""
