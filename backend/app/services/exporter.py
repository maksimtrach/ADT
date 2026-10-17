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
    for warning in validation["warnings"]:
        controls.append(["Предупреждение", warning])
    for key, value in validation["calculated_sums"].items():
        controls.append([f"Сумма: {key}", value])
    for cell in controls[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="001529")
    controls.column_dimensions["A"].width = 34
    controls.column_dimensions["B"].width = 80
    workbook.save(path)

