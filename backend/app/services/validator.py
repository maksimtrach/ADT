from __future__ import annotations

from collections import defaultdict
from collections import Counter
from decimal import Decimal
from typing import Any

from backend.app.models.domain import ControlStatus, TransformationSpec
from backend.app.services.parser import ParseResult
from backend.app.services.account_structure import MONEY_KEYS


def validate_result(parsed: ParseResult, spec: TransformationSpec) -> dict[str, Any]:
    warnings = list(spec.notes)
    required = [column for column in spec.target_columns if column.required]
    missing_required: dict[str, int] = {}
    for column in required:
        count = sum(row.get(column.key) in (None, "") for row in parsed.rows)
        if count:
            missing_required[column.key] = count

    sums: dict[str, Decimal] = defaultdict(Decimal)
    for column in spec.target_columns:
        if column.kind == "number":
            for row in parsed.rows:
                if not row.get("_control_included", True):
                    continue
                value = row.get(column.key)
                if isinstance(value, (int, float)):
                    sums[column.key] += Decimal(str(value))

    comparisons: dict[str, dict[str, Any]] = {}
    aggregation = "root_accounts" if spec.row_rules.account_tree else "all_rows"
    control_count = sum(row.get("_control_included", True) for row in parsed.rows)
    scope_excluded_rows = []
    ambiguous_accounts = [row["_source_row"] for row in parsed.rows if row.get("_ambiguous_account")]
    # Only compare two documented scopes, never search arbitrary subsets to force a match.
    if (spec.row_rules.account_tree and not ambiguous_accounts
            and set(MONEY_KEYS).issubset(parsed.declared_totals)
            and any(abs(sums.get(k, Decimal(0)) - Decimal(str(parsed.declared_totals[k]))) > Decimal("0.01") for k in MONEY_KEYS)):
        balance_rows = [row for row in parsed.rows if row.get("_control_included", True)
                        and row.get("_row_kind", "account") == "account"
                        and str(row.get("account", "")).isdigit() and len(str(row["account"])) == 2]
        balance_sums = {k: sum((Decimal(str(row.get(k) or 0)) for row in balance_rows), Decimal(0)) for k in MONEY_KEYS}
        if balance_rows and all(abs(balance_sums[k] - Decimal(str(parsed.declared_totals[k]))) <= Decimal("0.01") for k in MONEY_KEYS):
            sums = balance_sums
            aggregation = "two_digit_root_accounts"
            control_count = len(balance_rows)
            included = {row["_source_row"] for row in balance_rows}
            scope_excluded_rows = [row["_source_row"] for row in parsed.rows
                                   if row.get("_control_included", True) and row["_source_row"] not in included]
            warnings.append("Все шесть итогов совпали с двухзначными корневыми счетами; забалансовые и дополнительные счета сохранены вне этой сверки")
    if ambiguous_accounts:
        sums.clear()  # Scope of totals is unknown when account identities collide.
    failed_controls = 0
    for key, declared in parsed.declared_totals.items():
        actual = sums.get(key, Decimal(0))
        difference = round(float(actual - Decimal(str(declared))), 2)
        passed = abs(difference) <= 0.01 and not ambiguous_accounts
        comparisons[key] = {"declared": declared, "calculated": float(round(actual, 2)), "difference": difference, "passed": passed}
        if ambiguous_accounts:
            comparisons[key].update(calculated=None, difference=None)
        if not passed:
            failed_controls += 1

    money_keys = ("opening_debit", "opening_credit", "turnover_debit", "turnover_credit", "closing_debit", "closing_credit")
    balance_failures = []
    balance_checked = 0
    if all(spec.column_mapping.get(key) is not None for key in money_keys):
        for row in parsed.rows:
            if not any(row.get(key) not in (None, 0) for key in money_keys):
                continue
            balance_checked += 1
            values = [Decimal(str(row.get(key) or 0)) for key in money_keys]
            difference = values[0] - values[1] + values[2] - values[3] - values[4] + values[5]
            if abs(difference) > Decimal("0.01"):
                balance_failures.append({"row": row["_source_row"], "difference": float(difference)})
    duplicates = []
    if spec.row_rules.account_tree:
        counts = Counter(row.get("account") for row in parsed.rows
                         if row.get("account") and row.get("_row_kind", "account") == "account")
        duplicates = [code for code, count in counts.items() if count > 1]

    classifications = dict(Counter(row.get("_row_kind", "account") for row in parsed.rows))
    uncertain_rows = sum(count for kind, count in classifications.items() if kind != "account")
    if missing_required or failed_controls or parsed.numeric_errors:
        status = ControlStatus.FAILED
    elif (parsed.unprocessed_rows or spec.confidence < 0.65 or balance_failures or duplicates or not comparisons
          or uncertain_rows or ambiguous_accounts or spec.row_rules.standard_money_order or scope_excluded_rows):
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
    if balance_failures:
        warnings.append(f"Нарушено построчное равенство сальдо и оборотов: {len(balance_failures)}")
    if duplicates:
        warnings.append("Повторяющиеся коды счетов сохранены отдельно: " + ", ".join(duplicates))
    if uncertain_rows:
        warnings.append(f"Строки аналитики, валютные и служебные строки сохранены без повторного включения в общие суммы: {uncertain_rows}")
    if ambiguous_accounts:
        warnings.append("Неоднозначные исходные коды счетов: общие суммы не рассчитаны; требуется уточнение ведущих нулей и области суммирования")

    return {
        "status": status.value,
        "warnings": list(dict.fromkeys(warnings)),
        "missing_required": missing_required,
        "numeric_errors": parsed.numeric_errors[:100],
        "unprocessed_row_numbers": parsed.unprocessed_rows[:200],
        "calculated_sums": {key: float(round(value, 2)) for key, value in sums.items()},
        "aggregation": aggregation,
        "control_row_count": control_count,
        "control_rows": parsed.control_rows,
        "scope_excluded_rows": scope_excluded_rows,
        "control_comparisons": comparisons,
        "row_balance": {"checked": balance_checked, "failed": len(balance_failures), "examples": balance_failures[:20]},
        "duplicate_accounts": duplicates,
        "independent_totals_available": bool(comparisons),
        "row_classification": classifications,
        "ambiguous_account_rows": ambiguous_accounts,
        "aggregation_available": not ambiguous_accounts,
    }
