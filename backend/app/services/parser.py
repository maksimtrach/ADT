from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.models.domain import DataType, TransformationSpec
from backend.app.services.account_structure import annotate_account_rows
from backend.app.services.table_regions import is_blank, trailing_control


@dataclass
class ParseResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    unprocessed_rows: list[int] = field(default_factory=list)
    skipped_rows: list[int] = field(default_factory=list)
    numeric_errors: list[dict[str, Any]] = field(default_factory=list)
    declared_totals: dict[str, float] = field(default_factory=dict)
    control_rows: list[int] = field(default_factory=list)


def normalize_number(value: Any) -> tuple[float | None, bool]:
    if is_blank(value):
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float, Decimal)):
        return float(value), False
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if re.search(r"[a-zа-яё]", text, re.IGNORECASE):
        return None, True
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


def _account_string(value: Any, cell) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        number_format = cell.number_format
        if re.fullmatch(r"0+", number_format):
            return str(int(value)).zfill(len(number_format))
    return _string(value)


def _annotate_account_tree(rows: list[dict[str, Any]]) -> None:
    # Restore a root's width only when its child codes provide unambiguous evidence.
    prefixes = {str(row.get("account")).split(".")[0] for row in rows if "." in str(row.get("account"))}
    for row in rows:
        code = row.get("account") or ""
        matches = [prefix for prefix in prefixes if prefix.isdigit() and code.isdigit() and int(prefix) == int(code)]
        if "." not in code and len(matches) == 1:
            row["account"] = matches[0]
    codes = {row.get("account") for row in rows if row.get("account")}
    for row in rows:
        code = row.get("account") or ""
        parts = code.split(".")
        ancestors = [".".join(parts[:index]) for index in range(1, len(parts))]
        parent = next((item for item in reversed(ancestors) if item in codes), None)
        has_children = any(other.startswith(code + ".") for other in codes) if code else False
        row["parent_account"] = parent
        row["account_level"] = str(len(parts)) if code else None
        row["row_type"] = "Сводный счёт" if has_children else "Детальный счёт"
        row["_control_included"] = parent is None


ACCOUNT_PATTERN = re.compile(r"^\d{1,3}(?:\.\d+)*$")


def _hierarchy_level_index(
    value: str,
    cell,
    worksheet,
    row_number: int,
    numeric_count: int,
    current_depth: int,
    last_depth: int,
) -> int:
    indent = int(cell.alignment.indent or 0)
    outline = int(worksheet.row_dimensions[row_number].outlineLevel or 0)
    visual_depth = max(indent, outline)
    if visual_depth > 0:
        return min(visual_depth, last_depth)
    if ACCOUNT_PATTERN.fullmatch(value.strip()):
        return 0
    if numeric_count > 0:
        return last_depth
    if cell.font.bold and last_depth > 1:
        return 1
    if current_depth <= 0:
        return 1
    if current_depth >= last_depth:
        return 1
    return current_depth + 1


def parse_workbook(path: Path, spec: TransformationSpec, data_type: DataType, *, workbook=None) -> ParseResult:
    owns_workbook = workbook is None
    if owns_workbook:
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
    hierarchy_levels = spec.hierarchy.levels if spec.hierarchy else []
    hierarchy_keys = {level.key for level in hierarchy_levels}
    hierarchy_depth = -1

    detected_control = trailing_control(worksheet, spec.column_mapping, spec.data_start_row, spec.data_end_row) if spec.row_rules.account_tree else None
    control_rows = set(spec.control_rows) | ({detected_control} if detected_control else set())
    for row_number in sorted(set(range(spec.data_start_row, spec.data_end_row + 1)) | control_rows):
        raw_values = [worksheet.cell(row_number, column).value for column in range(1, worksheet.max_column + 1)]
        if all(is_blank(value) for value in raw_values):
            result.skipped_rows.append(row_number)
            continue
        row_text = " ".join(str(value).strip().lower() for value in raw_values[:12] if value not in (None, ""))
        is_total = any(keyword.lower() in row_text for keyword in spec.row_rules.skip_keywords)

        extracted: dict[str, Any] = {}
        refs: dict[str, str] = {}
        numeric_count = 0
        for target in spec.target_columns:
            if target.key in hierarchy_keys:
                extracted[target.key] = None
                continue
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
                extracted[target.key] = (
                    _account_string(value, worksheet[coordinate])
                    if target.key in {"account", "subaccount", "debit_account", "credit_account"} and coordinate
                    else _string(value)
                )
            if extracted[target.key] not in (None, "") and coordinate:
                refs[target.key] = f"{worksheet.title}!{coordinate}"

        if (spec.row_rules.account_tree and row_number == spec.data_end_row
                and numeric_count == 0 and not extracted.get("account_name")):
            result.skipped_rows.append(row_number)
            continue

        unlabelled_total = (
            spec.row_rules.account_tree
            and (not spec.row_rules.classify_account_rows
                 or all(row.get("account") and row.get("account_name") for row in result.rows))
            and row_number == spec.data_end_row
            and not extracted.get("account")
            and not extracted.get("account_name")
            and numeric_count >= 2
            and all(value is None or isinstance(value, (int, float)) for value in raw_values)
        )
        if is_total or unlabelled_total or row_number in control_rows:
            for key in numeric_keys:
                if extracted.get(key) is not None:
                    result.declared_totals[key] = extracted[key]
            result.skipped_rows.append(row_number)
            result.control_rows.append(row_number)
            continue

        if spec.hierarchy:
            source_column = spec.hierarchy.source_column
            source_cell = worksheet.cell(row_number, source_column)
            hierarchy_value = source_cell.value
            hierarchy_coordinate = source_cell.coordinate
            if hierarchy_value in (None, "") and (row_number, source_column) in merged_values:
                hierarchy_value, hierarchy_coordinate = merged_values[(row_number, source_column)]
            hierarchy_text = _account_string(hierarchy_value, worksheet[hierarchy_coordinate])
            level_index = None
            if hierarchy_text:
                level_index = _hierarchy_level_index(
                    hierarchy_text,
                    source_cell,
                    worksheet,
                    row_number,
                    numeric_count,
                    hierarchy_depth,
                    len(hierarchy_levels) - 1,
                )
                hierarchy_depth = level_index
                level = hierarchy_levels[level_index]
                contexts[level.key] = (hierarchy_text, f"{worksheet.title}!{hierarchy_coordinate}")
                for child in hierarchy_levels[level_index + 1 :]:
                    contexts.pop(child.key, None)
            for level in hierarchy_levels:
                if level.key in contexts:
                    extracted[level.key], refs[level.key] = contexts[level.key]
            if hierarchy_text and (numeric_count == 0 or level_index != len(hierarchy_levels) - 1):
                result.skipped_rows.append(row_number)
                continue
        else:
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

        if spec.row_rules.classify_account_rows:
            for source_key, key in (("source_code", "account"), ("source_name", "account_name")):
                extracted[source_key] = extracted.get(key)
                if key in refs:
                    refs[source_key] = refs[key]

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
        if spec.row_rules.classify_account_rows and extracted.get("account_name"):
            meaningful = True  # Preserve labelled service/currency rows even without amounts.
        if data_type == DataType.JOURNAL:
            meaningful = any(extracted.get(key) not in (None, "") for key in extracted)
        if not meaningful:
            result.unprocessed_rows.append(row_number)
            continue
        extracted["_source_row"] = row_number
        extracted["_source_refs"] = refs
        result.rows.append(extracted)

    if spec.row_rules.account_tree:
        if spec.row_rules.classify_account_rows:
            annotate_account_rows(result.rows)
        else:
            _annotate_account_tree(result.rows)
    if owns_workbook:
        workbook.close()
    return result
