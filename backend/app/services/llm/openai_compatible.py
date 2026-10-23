from __future__ import annotations

from typing import Any

import httpx

from backend.app.services.llm.base import LLMAdapter


class OpenAICompatibleAdapter(LLMAdapter):
    def available(self) -> bool:
        if not self.profile.enabled or not self.credentials_available():
            return False
        try:
            with httpx.Client(timeout=5, trust_env=False, headers=self.headers()) as client:
                response = client.get(f"{self.profile.base_url}/models")
                response.raise_for_status()
            model_ids = {item.get("id") for item in response.json().get("data", [])}
            return self.profile.model in model_ids
        except Exception:
            return False

    def build_request(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "stream": False,
            "temperature": self.profile.generation.temperature,
            "max_tokens": self.profile.generation.max_tokens,
            "messages": messages,
        }
        if self.profile.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        return f"{self.profile.base_url}/chat/completions", payload

    def extract_content(self, response_payload: dict[str, Any]) -> str:
        content = response_payload["choices"][0]["message"].get("content")
        return content if isinstance(content, str) else ""
