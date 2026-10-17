from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.models.domain import DataType, TransformationSpec, ValueOverride
from backend.app.services.rule_planner import rule_plan


class FeedbackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_mapping: dict[str, int | None] | None = None
    value_overrides: list[ValueOverride] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OllamaPlanner:
    def __init__(self) -> None:
        self.url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model

    @property
    def enabled(self) -> bool:
        return settings.llm_provider.lower() == "ollama"

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            response = httpx.get(f"{self.url}/api/tags", timeout=5, trust_env=False)
            response.raise_for_status()
            names = {item.get("name") for item in response.json().get("models", [])}
            return self.model in names
        except Exception:
            return False

    def refine(
        self,
        profile: dict[str, Any],
        baseline: TransformationSpec,
        data_type: DataType,
        feedback: str | None = None,
    ) -> TransformationSpec:
        if not self.enabled:
            raise AppError(503, "LLM отключена в конфигурации", "LLM_UNAVAILABLE")
        if feedback:
            task = (
                "Преобразуй замечание пользователя в минимальный JSON patch. "
                "Для значения поля на строке или диапазоне обязательно используй value_overrides. "
                "Если названа одна строка, оставь to_row null — это означает только from_row. "
                "Для открытого диапазона явно установи to_row равным data_end_row. "
                "Пример: 'строки 4-8 относятся к счету 62.02' означает "
                "value_overrides=[{target_field:'account',from_row:4,to_row:8,value:'62.02'}]."
            )
            schema_model: type[BaseModel] = FeedbackPatch
        else:
            task = "Проверь и при необходимости исправь спецификацию бухгалтерского XLSX. Верни только JSON по схеме."
            schema_model = TransformationSpec
        prompt = {
            "task": task,
            "constraints": [
                "Не создавай Python-код.",
                "Используй только существующие листы, строки, колонки и ключи целевой схемы.",
                "Не выдумывай отсутствующие исходные поля.",
                "Нумерация строк и колонок начинается с 1.",
            ],
            "data_type": data_type.value,
            "user_feedback": feedback,
            "profile": profile,
            "baseline_spec": baseline.model_dump(mode="json"),
        }
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
                response = client.post(
                    f"{self.url}/api/chat",
                    json={
                        "model": self.model,
                        "stream": False,
                        "think": False,
                        "format": schema_model.model_json_schema(),
                        "keep_alive": "10m",
                        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1800},
                        "messages": [
                            {"role": "system", "content": "Ты возвращаешь только безопасный JSON по предоставленной схеме. Никакого кода и пояснений."},
                            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                        ],
                    },
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
                if feedback:
                    patch = FeedbackPatch.model_validate_json(content)
                    candidate = baseline.model_copy(deep=True)
                    valid_fields = {item.key for item in baseline.target_columns}
                    mapping_changes = patch.column_mapping or {}
                    if not set(mapping_changes).issubset(valid_fields):
                        raise AppError(422, "LLM изменила неизвестное поле", "INVALID_LLM_SPEC")
                    changed_mapping = any(candidate.column_mapping.get(key) != value for key, value in mapping_changes.items())
                    candidate.column_mapping.update(mapping_changes)
                    candidate.value_overrides = patch.value_overrides
                    candidate.notes = list(dict.fromkeys(candidate.notes + patch.notes))
                    if not changed_mapping and not patch.value_overrides:
                        raise AppError(422, "LLM не сформировала допустимых изменений", "LLM_NO_CHANGES")
                else:
                    candidate = TransformationSpec.model_validate_json(content)
        except AppError:
            raise
        except Exception as error:
            raise AppError(503, "Локальная LLM недоступна или вернула некорректный ответ", "LLM_UNAVAILABLE") from error

        sheet_names = {sheet["name"] for sheet in profile["sheets"]}
        if candidate.sheet_name not in sheet_names:
            raise AppError(422, "LLM указала отсутствующий лист", "INVALID_LLM_SPEC")
        valid_fields = {item.key for item in baseline.target_columns}
        if [item.key for item in candidate.target_columns] != [item.key for item in baseline.target_columns]:
            raise AppError(422, "LLM изменила целевую схему", "INVALID_LLM_SPEC")
        sheet_profile = next(sheet for sheet in profile["sheets"] if sheet["name"] == candidate.sheet_name)
        if candidate.data_start_row > candidate.data_end_row or candidate.data_end_row > sheet_profile["max_row"]:
            raise AppError(422, "LLM указала недопустимый диапазон строк", "INVALID_LLM_SPEC")
        if any(column is not None and (column < 1 or column > sheet_profile["max_column"]) for column in candidate.column_mapping.values()):
            raise AppError(422, "LLM указала недопустимую колонку", "INVALID_LLM_SPEC")
        if candidate.value_overrides and not feedback:
            raise AppError(422, "LLM добавила изменения без замечания пользователя", "INVALID_LLM_SPEC")
        for override in candidate.value_overrides:
            end_row = override.to_row or override.from_row
            if (
                override.target_field not in valid_fields
                or override.from_row < candidate.data_start_row
                or end_row > candidate.data_end_row
                or end_row < override.from_row
            ):
                raise AppError(422, "LLM сформировала недопустимое правило исправления", "INVALID_LLM_SPEC")
        return candidate


__all__ = ["OllamaPlanner", "rule_plan"]

