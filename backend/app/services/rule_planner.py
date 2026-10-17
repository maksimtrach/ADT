from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.core.errors import AppError
from backend.app.models.domain import DataType, RowRules, TransformationSpec
from backend.app.services.templates import get_template


ALIASES: dict[str, list[str]] = {
    "account": ["счет", "счёт", "счет учета", "номер счета", "account"],
    "subaccount": ["субсчет", "субсчёт", "суб счет"],
    "analytics": ["аналитика", "контрагент", "субконто", "наименование"],
    "opening_debit": ["сальдо на начало дт", "начальное сальдо дебет", "нач. сальдо дт"],
    "opening_credit": ["сальдо на начало кт", "начальное сальдо кредит", "нач. сальдо кт"],
    "turnover_debit": ["оборот дт", "обороты дебет", "дебетовый оборот"],
    "turnover_credit": ["оборот кт", "обороты кредит", "кредитовый оборот"],
    "closing_debit": ["сальдо на конец дт", "конечное сальдо дебет", "кон. сальдо дт"],
    "closing_credit": ["сальдо на конец кт", "конечное сальдо кредит", "кон. сальдо кт"],
    "date": ["дата", "дата операции", "период", "date"],
    "document_number": ["номер документа", "№ документа", "документ", "номер"],
    "debit_account": ["счет дт", "счёт дт", "дебет", "дт счет"],
    "credit_account": ["счет кт", "счёт кт", "кредит", "кт счет"],
    "debit_analytics": ["аналитика дт", "субконто дт", "контрагент дт"],
    "credit_analytics": ["аналитика кт", "субконто кт", "контрагент кт"],
    "amount": ["сумма", "сумма операции", "оборот", "amount"],
    "description": ["содержание операции", "описание", "назначение", "комментарий"],
}


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[\n\r]+", " ", text)
    return re.sub(r"\s+", " ", text)


def alias_score(value: str, aliases: list[str]) -> float:
    if not value:
        return 0
    best = 0.0
    for alias in aliases:
        candidate = normalize_header(alias)
        if value == candidate:
            return 1.0
        if candidate in value or value in candidate:
            best = max(best, 0.86)
        best = max(best, SequenceMatcher(None, value, candidate).ratio() * 0.78)
    return best


def rule_plan(path: Path, data_type: DataType, template_id: str) -> TransformationSpec:
    template = get_template(template_id)
    workbook = load_workbook(path, data_only=False, read_only=True, keep_links=False)
    best: tuple[float, str, int, dict[str, int | None], int] | None = None
    for worksheet in workbook.worksheets:
        max_scan_column = min(worksheet.max_column, 120)
        for row_number in range(1, worksheet.max_row + 1):
            values = [normalize_header(worksheet.cell(row_number, column).value) for column in range(1, max_scan_column + 1)]
            mapping: dict[str, int | None] = {}
            scores: list[float] = []
            used: set[int] = set()
            for target in template.columns:
                matches = [(alias_score(value, ALIASES.get(target.key, [target.label])), index + 1) for index, value in enumerate(values) if index + 1 not in used]
                score, column = max(matches, default=(0.0, 0))
                if score >= 0.55:
                    mapping[target.key] = column
                    scores.append(score)
                    used.add(column)
                else:
                    mapping[target.key] = None
            matched = sum(value is not None for value in mapping.values())
            required_matched = sum(mapping[column.key] is not None for column in template.columns if column.required)
            total_required = sum(column.required for column in template.columns)
            aggregate = matched + required_matched * 1.5 + sum(scores) / max(len(scores), 1)
            if total_required and required_matched == 0:
                aggregate *= 0.25
            if best is None or aggregate > best[0]:
                best = (aggregate, worksheet.title, row_number, mapping, worksheet.max_row)
    workbook.close()
    if not best or best[0] < 1:
        raise AppError(422, "Не удалось определить строку заголовков", "STRUCTURE_NOT_RECOGNIZED")
    _, sheet_name, header_row, mapping, end_row = best
    matched = sum(value is not None for value in mapping.values())
    confidence = min(0.98, matched / max(len(template.columns), 1) + 0.18)
    inherit = ["account", "subaccount", "analytics"] if data_type == DataType.TRIAL_BALANCE else []
    notes = [f"Автоматически сопоставлено полей: {matched} из {len(template.columns)}"]
    missing = [column.label for column in template.columns if mapping[column.key] is None]
    if missing:
        notes.append("Не найдены поля: " + ", ".join(missing))
    return TransformationSpec(
        sheet_name=sheet_name,
        header_row=header_row,
        data_start_row=header_row + 1,
        data_end_row=end_row,
        column_mapping=mapping,
        target_columns=template.columns,
        row_rules=RowRules(inherit_fields=inherit),
        confidence=confidence,
        notes=notes,
    )

