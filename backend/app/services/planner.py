from __future__ import annotations

import json
import httpx
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from openpyxl.utils import get_column_letter

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, get_llm_configuration
from backend.app.models.domain import DataType, TransformationSpec, ValueOverride
from backend.app.services.llm import LLMAdapter, build_adapter
from backend.app.services.rule_planner import rule_plan
from backend.app.services.account_structure import DERIVED_ACCOUNT_FIELDS
from backend.app.services.llm_context import context_rows, source_context
from backend.app.services.candidate_choice import candidate_request
from backend.app.services.llm.budget import LLMTimeBudget, LLMRequestError, transport_error


class FeedbackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_mapping: dict[str, int | None] | None = None
    value_overrides: list[ValueOverride] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CandidateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: int | None = Field(..., strict=True)
    reason: str = Field(max_length=500)


TraceCallback = Callable[[str, dict[str, Any]], None]


def compact_profile(profile: dict[str, Any], baseline: TransformationSpec, source_path=None) -> dict[str, Any]:
    if source_path is not None:
        return source_context(source_path, [baseline])
    sheets = []
    for sheet in profile["sheets"]:
        if sheet["name"] != baseline.sheet_name:
            continue
        samples = sheet.get("sample_rows", [])
        headers = [(baseline.header_start_row or baseline.header_row, baseline.header_end_row or baseline.header_row)]
        selected_rows = context_rows(sheet["max_row"], headers)
        selected = {row["row"]: row for row in samples if row["row"] in selected_rows}
        sheets.append({
            "name": sheet["name"], "max_row": sheet["max_row"], "max_column": sheet["max_column"],
            "columns": {get_column_letter(i): i for i in range(1, sheet["max_column"] + 1)},
            "sample_rows": [
                {
                    "row": row["row"],
                    "cells": {f"{get_column_letter(i)}{row['row']}": value
                              for i, value in enumerate(row["values"], 1) if value is not None},
                    "indents": row.get("indents", []),
                    "outline_level": row.get("outline_level", 0),
                    "formula_columns": row.get("formula_columns", []),
                } for row in selected.values()
            ],
            "missing_context_rows": [row for row in selected_rows if row not in selected],
        })
    return {"sheet_count": len(sheets), "sheets": sheets}


class LLMPlanner:
    def __init__(self, configuration: LLMConfiguration | None = None) -> None:
        self.configuration = configuration or get_llm_configuration()
        self.adapters: list[LLMAdapter] = [
            build_adapter(name, self.configuration.profiles[name])
            for name in self.configuration.profile_order()
            if self.configuration.profiles[name].enabled
        ]

        self.budget = LLMTimeBudget(self.configuration.total_timeout_seconds)
        for adapter in self.adapters:
            adapter.budget = self.budget

    @property
    def enabled(self) -> bool:
        return bool(self.adapters)

    @property
    def active_adapter(self) -> LLMAdapter | None:
        return self.adapters[0] if self.adapters else None

    @property
    def url(self) -> str:
        return self.active_adapter.profile.base_url if self.active_adapter else ""

    @property
    def model(self) -> str:
        return self.active_adapter.profile.model if self.active_adapter else ""

    def available(self) -> bool:
        return any(adapter.available() for adapter in self.adapters)

    def health(self) -> dict[str, Any]:
        profiles: list[dict[str, Any]] = []
        selected_profile: str | None = None
        for adapter in self.adapters:
            is_available = adapter.available()
            if is_available and selected_profile is None:
                selected_profile = adapter.profile_name
            profiles.append({**adapter.metadata, "available": is_available})
        active = self.active_adapter
        return {
            "active_profile": self.configuration.selected_profile,
            "selected_profile": selected_profile,
            "protocol": active.profile.protocol if active else None,
            "model": active.profile.model if active else None,
            "base_url": active.profile.base_url if active else None,
            "available": selected_profile is not None,
            "profiles": profiles,
        }

    def choose_candidate(self, selection, trace: TraceCallback | None = None, source_path=None):
        # The model can choose a verified interpretation or abstain. It cannot
        # rewrite column positions, rows, constraints or values.
        request = candidate_request(selection, source_path)
        if request is None:
            raise AppError(422, "Ни один вариант не прошёл проверки данных", "STRUCTURE_AMBIGUOUS")
        allowed = {item["candidate_id"] for item in request["candidates"]}
        schema = CandidateChoice.model_json_schema()
        schema["properties"]["candidate_id"] = {
            "anyOf": [{"type": "integer", "enum": sorted(allowed)}, {"type": "null"}],
        }
        for index, adapter in enumerate(self.adapters):
            content = json.dumps(request, ensure_ascii=False)
            if adapter.profile.no_think_suffix:
                content += "\n/no_think"
            try:
                result = adapter.complete(
                    [{"role": "system", "content": "Выбирай только из предоставленных вариантов. Верни JSON."},
                     {"role": "user", "content": content}],
                    schema, "CandidateChoice", trace,
                )
            except (httpx.HTTPError, OSError, RuntimeError, LLMRequestError) as error:
                if index + 1 < len(self.adapters) and self.budget.remaining > 0:
                    if trace:
                        trace("LLM_PROFILE_FALLBACK", {"from_profile": adapter.profile_name,
                              "to_profile": self.adapters[index + 1].profile_name, "reason": str(error)[:300]})
                    continue
                if isinstance(error, AppError):
                    raise
                raise transport_error(adapter, error) from error
            try:
                choice = CandidateChoice.model_validate_json(result.content)
            except ValueError as error:
                raise AppError(422, "Модель не вернула допустимый выбор", "INVALID_LLM_SPEC") from error
            if choice.candidate_id not in allowed:
                raise AppError(422, "Не удалось однозначно выбрать структуру; укажите исходную колонку счёта", "STRUCTURE_AMBIGUOUS")
            if trace:
                trace("LLM_CANDIDATE_SELECTED", {**adapter.metadata, **choice.model_dump()})
            return selection.candidates[choice.candidate_id]
        raise AppError(503, "Нет включённых LLM-профилей", "LLM_UNAVAILABLE")

    def recognize_structure(self, path, data_type, template_id, selection, trace=None, feedback=None):
        from backend.app.services.structure_recovery import recognize_structure
        return recognize_structure(self, path, data_type, template_id, selection, trace, feedback)

    def refine(
        self,
        profile: dict[str, Any],
        baseline: TransformationSpec,
        data_type: DataType,
        feedback: str | None = None,
        trace: TraceCallback | None = None,
        source_path=None,
    ) -> TransformationSpec:
        if not self.enabled:
            raise AppError(503, "LLM отключена в конфигурации", "LLM_UNAVAILABLE")

        task, schema_model = self._task_and_schema(feedback)
        prompt = {
            "task": task,
            "constraints": [
                "Не создавай Python-код.",
                "Используй только существующие листы, строки, колонки и ключи целевой схемы.",
                "Не выдумывай отсутствующие исходные поля.",
                "Нумерация строк и колонок начинается с 1.",
                "Не изменяй состав target_columns и порядок уровней hierarchy.",
                "Индексы колонок абсолютные: A=1, B=2, C=3; пустые и объединённые колонки не сдвигают нумерацию.",
                "Не включай строки шапки в данные. Денежные колонки должны содержать числа, а не наименования.",
            ],
            "data_type": data_type.value,
            "user_feedback": feedback,
            "profile": compact_profile(profile, baseline, source_path),
            "baseline_spec": baseline.model_dump(mode="json"),
        }

        failures: list[tuple[LLMAdapter, Exception]] = []
        for index, adapter in enumerate(self.adapters):
            user_content = json.dumps(prompt, ensure_ascii=False)
            if adapter.profile.no_think_suffix:
                user_content += "\n/no_think"
            messages = [
                {
                    "role": "system",
                    "content": "Ты возвращаешь только безопасный JSON по предоставленной схеме. Никакого кода и пояснений.",
                },
                {"role": "user", "content": user_content},
            ]
            response_received = False
            try:
                result = adapter.complete(
                    messages,
                    schema_model.model_json_schema(),
                    schema_model.__name__,
                    trace=trace,
                )
                response_received = True
                candidate = self._candidate_from_content(result.content, baseline, feedback)
                self._validate_candidate(candidate, baseline, profile, feedback)
                if trace:
                    trace(
                        "LLM_SPEC_ACCEPTED",
                        {**adapter.metadata, "spec": candidate.model_dump(mode="json")},
                    )
                return candidate
            except AppError as error:
                if trace:
                    trace(
                        "LLM_VALIDATION_FAILED",
                        {**adapter.metadata, "code": error.code, "message": error.message},
                    )
                failures.append((adapter, error))
            except (ValidationError, ValueError, KeyError, IndexError, TypeError) as error:
                if response_received and trace:
                    trace(
                        "LLM_VALIDATION_FAILED",
                        {
                            **adapter.metadata,
                            "error_type": type(error).__name__,
                            "message": str(error)[:1000],
                        },
                    )
                failures.append((adapter, AppError(
                    422, f"Модель «{adapter.profile.model}» вернула некорректный ответ: {type(error).__name__}",
                    "INVALID_LLM_RESPONSE")))
            except Exception as error:
                failures.append((adapter, error))

            if self.budget.remaining <= 0:
                break
            if index + 1 < len(self.adapters) and trace:
                next_adapter = self.adapters[index + 1]
                trace(
                    "LLM_PROFILE_FALLBACK",
                    {
                        "from_profile": adapter.profile_name,
                        "to_profile": next_adapter.profile_name,
                        "reason": str(failures[-1][1])[:1000],
                    },
                )

        last_error = failures[-1][1] if failures else None
        if isinstance(last_error, AppError):
            raise last_error
        raise AppError(
            503,
            "LLM недоступна или вернула некорректный ответ во всех настроенных профилях",
            "LLM_UNAVAILABLE",
        ) from last_error

    @staticmethod
    def _task_and_schema(feedback: str | None) -> tuple[str, type[BaseModel]]:
        if feedback:
            return (
                "Преобразуй замечание пользователя в минимальный JSON patch. "
                "Для изменения источника поля используй column_mapping (например счет из C: account=3); "
                "никогда не заменяй смену колонки перечнем value_overrides. "
                "Для значения поля на строке или диапазоне обязательно используй value_overrides. "
                "Если названа одна строка, оставь to_row null — это означает только from_row. "
                "Для открытого диапазона явно установи to_row равным data_end_row. "
                "Пример: 'строки 4-8 относятся к счету 62.02' означает "
                "value_overrides=[{target_field:'account',from_row:4,to_row:8,value:'62.02'}].",
                FeedbackPatch,
            )
        return (
            "Проверь и при необходимости исправь спецификацию бухгалтерского XLSX. "
            "Верни только JSON по схеме.",
            TransformationSpec,
        )

    @staticmethod
    def _candidate_from_content(
        content: str,
        baseline: TransformationSpec,
        feedback: str | None,
    ) -> TransformationSpec:
        if not feedback:
            return TransformationSpec.model_validate_json(content)

        patch = FeedbackPatch.model_validate_json(content)
        candidate = baseline.model_copy(deep=True)
        valid_fields = {item.key for item in baseline.target_columns}
        mapping_changes = patch.column_mapping or {}
        if not set(mapping_changes).issubset(valid_fields):
            raise AppError(422, "LLM изменила неизвестное поле", "INVALID_LLM_SPEC")
        changed_mapping = any(candidate.column_mapping.get(key) != value for key, value in mapping_changes.items())
        candidate.column_mapping.update(mapping_changes)
        candidate.value_overrides = [
            item for item in candidate.value_overrides if item.target_field not in mapping_changes
        ] + patch.value_overrides
        candidate.notes = list(dict.fromkeys(candidate.notes + patch.notes))
        if not changed_mapping and not patch.value_overrides:
            raise AppError(422, "LLM не сформировала допустимых изменений", "LLM_NO_CHANGES")
        return candidate

    @staticmethod
    def _validate_candidate(
        candidate: TransformationSpec,
        baseline: TransformationSpec,
        profile: dict[str, Any],
        feedback: str | None,
    ) -> None:
        sheet_names = {sheet["name"] for sheet in profile["sheets"]}
        if candidate.sheet_name not in sheet_names:
            raise AppError(422, "LLM указала отсутствующий лист", "INVALID_LLM_SPEC")
        valid_fields = {item.key for item in baseline.target_columns}
        if candidate.target_columns != baseline.target_columns:
            raise AppError(422, "LLM изменила целевую схему", "INVALID_LLM_SPEC")
        if candidate.control_rows != baseline.control_rows:
            raise AppError(422, "LLM изменила итоговые строки", "INVALID_LLM_SPEC")
        if candidate.row_rules.account_tree != baseline.row_rules.account_tree:
            raise AppError(422, "LLM изменила правила суммирования счетов", "INVALID_LLM_SPEC")
        if candidate.row_rules.classify_account_rows != baseline.row_rules.classify_account_rows:
            raise AppError(422, "LLM изменила классификацию валютных строк", "INVALID_LLM_SPEC")
        if baseline.row_rules.account_tree and any(
            candidate.column_mapping.get(key) is not None for key in DERIVED_ACCOUNT_FIELDS
        ):
            raise AppError(422, "LLM сопоставила вычисляемое поле исходной колонке", "INVALID_LLM_SPEC")
        sheet_profile = next(sheet for sheet in profile["sheets"] if sheet["name"] == candidate.sheet_name)
        header_start = candidate.header_start_row or candidate.header_row
        header_end = candidate.header_end_row or candidate.header_row
        if header_start > header_end or header_end >= candidate.data_start_row:
            raise AppError(422, "LLM указала недопустимый диапазон шапки", "INVALID_LLM_SPEC")
        if candidate.data_start_row > candidate.data_end_row or candidate.data_end_row > sheet_profile["max_row"]:
            raise AppError(422, "LLM указала недопустимый диапазон строк", "INVALID_LLM_SPEC")
        if not set(candidate.column_mapping).issubset(valid_fields):
            raise AppError(422, "LLM добавила неизвестное сопоставление поля", "INVALID_LLM_SPEC")
        if any(
            column is not None and (column < 1 or column > sheet_profile["max_column"])
            for column in candidate.column_mapping.values()
        ):
            raise AppError(422, "LLM указала недопустимую колонку", "INVALID_LLM_SPEC")
        baseline_levels = [level.key for level in baseline.hierarchy.levels] if baseline.hierarchy else []
        candidate_levels = [level.key for level in candidate.hierarchy.levels] if candidate.hierarchy else []
        if candidate_levels != baseline_levels:
            raise AppError(422, "LLM изменила структуру аналитических уровней", "INVALID_LLM_SPEC")
        if candidate.hierarchy and (
            candidate.hierarchy.source_column > sheet_profile["max_column"]
            or not candidate.hierarchy.levels
            or candidate.hierarchy.levels[0].key != "account"
            or not set(candidate_levels).issubset(valid_fields)
        ):
            raise AppError(422, "LLM указала недопустимую иерархию аналитик", "INVALID_LLM_SPEC")
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


# Совместимость для старых импортов и сторонних тестов.
OllamaPlanner = LLMPlanner


__all__ = ["FeedbackPatch", "LLMPlanner", "OllamaPlanner", "rule_plan"]
