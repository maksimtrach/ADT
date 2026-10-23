from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from backend.app.models.domain import TransformationSpec


def export_result(
    path: Path,
    rows: list[dict[str, Any]],
    spec: TransformationSpec,
    validation: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Результат"
    headers = [column.label for column in spec.target_columns] + ["Источник"]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1677FF")
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        source_refs = sorted(set(row.get("_source_refs", {}).values()))
        worksheet.append([row.get(column.key) for column in spec.target_columns] + [", ".join(source_refs)])
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for index, column in enumerate(spec.target_columns, start=1):
        worksheet.column_dimensions[worksheet.cell(1, index).column_letter].width = 22 if column.kind == "text" else 18
        if column.kind == "number":
            for row_number in range(2, worksheet.max_row + 1):
                worksheet.cell(row_number, index).number_format = '#,##0.00'
    worksheet.column_dimensions[worksheet.cell(1, len(headers)).column_letter].width = 32

    controls = workbook.create_sheet("Контроль")
    controls.append(["Показатель", "Значение"])
    controls.append(["Статус", validation["status"]])
    controls.append(["Обработано строк", len(rows)])
    controls.append(["Не обработано строк", len(validation["unprocessed_row_numbers"])])
    controls.append(["Независимая сверка итогов", "Есть" if validation.get("independent_totals_available") else "Итоги отсутствуют"])
    if validation.get("row_balance"):
        controls.append(["Проверено построчных равенств", validation["row_balance"]["checked"]])
        controls.append(["Нарушено построчных равенств", validation["row_balance"]["failed"]])
    if spec.row_rules.account_tree:
        if validation.get("aggregation_available", True):
            controls.append(["Способ суммирования", "Двухзначные корневые счета: область подтверждена всеми шестью итогами" if validation.get("aggregation") == "two_digit_root_accounts" else "Только верхний уровень счетов; субсчета и валютные расшифровки не прибавляются повторно"])
            controls.append(["Счетов в контрольной сумме", validation["control_row_count"]])
        else:
            controls.append(["Способ суммирования", "Общие суммы не рассчитаны: неоднозначные коды счетов"])
    for kind, count in validation.get("row_classification", {}).items():
        controls.append([f"Тип строк: {kind}", count])
    controls.append(["Контрольные строки источника", ", ".join(map(str, validation.get("control_rows", [])))])
    if validation.get("scope_excluded_rows"):
        controls.append(["Корневые строки вне области сверки", ", ".join(map(str, validation["scope_excluded_rows"]))])
    for warning in validation["warnings"]:
        controls.append(["Предупреждение", warning])
    for key, value in validation["calculated_sums"].items():
        controls.append([f"Сумма: {key}", value])
    for key, comparison in validation["control_comparisons"].items():
        controls.append([f"Итог источника: {key}", comparison["declared"]])
        controls.append([f"Расхождение: {key}", comparison["difference"]])
    for cell in controls[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="001529")
    controls.column_dimensions["A"].width = 34
    controls.column_dimensions["B"].width = 80
    workbook.save(path)
