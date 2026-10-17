from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.app.models.domain import ControlStatus, TransformationSpec
from backend.app.services.parser import ParseResult


def validate_result(parsed: ParseResult, spec: TransformationSpec) -> dict[str, Any]:
    warnings = list(spec.notes)
    required = [column for column in spec.target_columns if column.required]
    missing_required: dict[str, int] = {}
    for column in required:
        count = sum(row.get(column.key) in (None, "") for row in parsed.rows)
        if count:
            missing_required[column.key] = count

    sums: dict[str, float] = defaultdict(float)
    for column in spec.target_columns:
        if column.kind == "number":
            for row in parsed.rows:
                value = row.get(column.key)
                if isinstance(value, (int, float)):
                    sums[column.key] += float(value)

    comparisons: dict[str, dict[str, Any]] = {}
    failed_controls = 0
    for key, declared in parsed.declared_totals.items():
        actual = sums.get(key, 0.0)
        difference = round(actual - declared, 2)
        passed = abs(difference) <= 0.01
        comparisons[key] = {"declared": declared, "calculated": round(actual, 2), "difference": difference, "passed": passed}
        if not passed:
            failed_controls += 1

    if missing_required or failed_controls:
        status = ControlStatus.FAILED
    elif parsed.numeric_errors or parsed.unprocessed_rows or spec.confidence < 0.65:
        status = ControlStatus.WARNINGS
    elif comparisons or parsed.rows:
        status = ControlStatus.PASSED
    else:
        status = ControlStatus.NOT_PERFORMED

    if missing_required:
        warnings.append("В обязательных полях обнаружены пропуски")
    if parsed.numeric_errors:
        warnings.append(f"Не удалось преобразовать числовые значения: {len(parsed.numeric_errors)}")
    if parsed.unprocessed_rows:
        warnings.append(f"Не обработано строк: {len(parsed.unprocessed_rows)}")
    if failed_controls:
        warnings.append(f"Контрольные суммы не сошлись: {failed_controls}")
    if not comparisons:
        warnings.append("Итоговые строки для независимой сверки не обнаружены")

    return {
        "status": status.value,
        "warnings": list(dict.fromkeys(warnings)),
        "missing_required": missing_required,
        "numeric_errors": parsed.numeric_errors[:100],
        "unprocessed_row_numbers": parsed.unprocessed_rows[:200],
        "calculated_sums": {key: round(value, 2) for key, value in sums.items()},
        "control_comparisons": comparisons,
    }

