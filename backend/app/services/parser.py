from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.models.domain import DataType, TransformationSpec


@dataclass
class ParseResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    unprocessed_rows: list[int] = field(default_factory=list)
    skipped_rows: list[int] = field(default_factory=list)
    numeric_errors: list[dict[str, Any]] = field(default_factory=list)
    declared_totals: dict[str, float] = field(default_factory=dict)


def normalize_number(value: Any) -> tuple[float | None, bool]:
    if value is None or value == "":
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float, Decimal)):
        return float(value), False
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    text = re.sub(r"[^0-9.\-+]", "", text)
    try:
        result = float(Decimal(text))
        return (-result if negative else result), False
    except (InvalidOperation, ValueError):
        return None, True


def normalize_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()
    for pattern in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_workbook(path: Path, spec: TransformationSpec, data_type: DataType) -> ParseResult:
    workbook = load_workbook(path, data_only=True, read_only=False, keep_links=False)
    worksheet = workbook[spec.sheet_name]
    result = ParseResult()
    merged_values: dict[tuple[int, int], tuple[Any, str]] = {}
    for merged in worksheet.merged_cells.ranges:
        top_left = worksheet.cell(merged.min_row, merged.min_col)
        for row_number in range(merged.min_row, merged.max_row + 1):
            for column_number in range(merged.min_col, merged.max_col + 1):
                merged_values[(row_number, column_number)] = (top_left.value, top_left.coordinate)

    contexts: dict[str, tuple[Any, str]] = {}
    target_kinds = {column.key: column.kind for column in spec.target_columns}
    numeric_keys = {key for key, kind in target_kinds.items() if kind == "number"}
    required_keys = {column.key for column in spec.target_columns if column.required}

    for row_number in range(spec.data_start_row, spec.data_end_row + 1):
        raw_values = [worksheet.cell(row_number, column).value for column in range(1, worksheet.max_column + 1)]
        if not any(value not in (None, "") for value in raw_values):
            result.skipped_rows.append(row_number)
            continue
        row_text = " ".join(str(value).strip().lower() for value in raw_values[:12] if value not in (None, ""))
        is_total = any(keyword.lower() in row_text for keyword in spec.row_rules.skip_keywords)

        extracted: dict[str, Any] = {}
        refs: dict[str, str] = {}
        numeric_count = 0
        for target in spec.target_columns:
            column = spec.column_mapping.get(target.key)
            value = None
            coordinate = ""
            if column is not None:
                cell = worksheet.cell(row_number, column)
                value = cell.value
                coordinate = cell.coordinate
                if value in (None, "") and (row_number, column) in merged_values:
                    value, coordinate = merged_values[(row_number, column)]
            if target.kind == "number":
                parsed, invalid = normalize_number(value)
                extracted[target.key] = parsed
                if parsed is not None:
                    numeric_count += 1
                if invalid:
                    result.numeric_errors.append({"row": row_number, "field": target.key, "cell": coordinate})
            elif target.kind == "date":
                extracted[target.key] = normalize_date(value)
            else:
                extracted[target.key] = _string(value)
            if extracted[target.key] not in (None, "") and coordinate:
                refs[target.key] = f"{worksheet.title}!{coordinate}"

        if is_total:
            for key in numeric_keys:
                if extracted.get(key) is not None:
                    result.declared_totals[key] = extracted[key]
            result.skipped_rows.append(row_number)
            continue

        if data_type == DataType.TRIAL_BALANCE and numeric_count == 0:
            context_updated = False
            for key in spec.row_rules.inherit_fields:
                value = extracted.get(key)
                if value not in (None, ""):
                    contexts[key] = (value, refs.get(key, f"{worksheet.title}!{row_number}"))
                    context_updated = True
            if context_updated:
                result.skipped_rows.append(row_number)
                continue

        explicit_account = extracted.get("account")
        if explicit_account not in (None, "") and contexts.get("account", (None, ""))[0] != explicit_account:
            contexts.pop("subaccount", None)
            contexts.pop("analytics", None)

        for key in spec.row_rules.inherit_fields:
            if extracted.get(key) in (None, "") and key in contexts:
                extracted[key], refs[key] = contexts[key]
            elif extracted.get(key) not in (None, ""):
                contexts[key] = (extracted[key], refs.get(key, f"{worksheet.title}!{row_number}"))

        for override in spec.value_overrides:
            end_row = override.to_row or override.from_row
            if override.from_row <= row_number <= end_row and override.target_field in target_kinds:
                kind = target_kinds[override.target_field]
                if kind == "number":
                    value, invalid = normalize_number(override.value)
                    if invalid:
                        result.numeric_errors.append({"row": row_number, "field": override.target_field, "cell": "USER_OVERRIDE"})
                elif kind == "date":
                    value = normalize_date(override.value)
                else:
                    value = _string(override.value)
                extracted[override.target_field] = value
                refs[override.target_field] = f"Правило пользователя: строки {override.from_row}–{end_row}"

        meaningful = numeric_count > 0 or any(extracted.get(key) not in (None, "") for key in required_keys)
        if data_type == DataType.JOURNAL:
            meaningful = any(extracted.get(key) not in (None, "") for key in extracted)
        if not meaningful:
            result.unprocessed_rows.append(row_number)
            continue
        extracted["_source_row"] = row_number
        extracted["_source_refs"] = refs
        result.rows.append(extracted)

    workbook.close()
    return result

