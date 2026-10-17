from __future__ import annotations

import math
import zipfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.core.errors import AppError


def validate_xlsx(path: Path, max_bytes: int) -> None:
    if path.stat().st_size > max_bytes:
        raise AppError(413, "Файл превышает допустимый размер", "FILE_TOO_LARGE")
    with path.open("rb") as source:
        if source.read(4) != b"PK\x03\x04":
            raise AppError(400, "Файл не является корректным XLSX", "INVALID_XLSX")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            total_uncompressed = sum(item.file_size for item in entries)
            if len(entries) > 5_000 or total_uncompressed > max(max_bytes * 20, 200 * 1024 * 1024):
                raise AppError(400, "XLSX содержит недопустимо большой архив", "UNSAFE_XLSX")
    except zipfile.BadZipFile as error:
        raise AppError(400, "Поврежденный XLSX-файл", "INVALID_XLSX") from error


def safe_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _sample_indexes(nonempty_rows: list[int], limit: int = 70) -> list[int]:
    if len(nonempty_rows) <= limit:
        return nonempty_rows
    selected = set(nonempty_rows[:25] + nonempty_rows[-15:])
    remaining = limit - len(selected)
    if remaining > 0:
        step = len(nonempty_rows) / remaining
        selected.update(nonempty_rows[min(math.floor(index * step), len(nonempty_rows) - 1)] for index in range(remaining))
    return sorted(selected)


def profile_workbook(path: Path) -> dict[str, Any]:
    try:
        workbook = load_workbook(path, data_only=False, read_only=False, keep_links=False)
    except Exception as error:
        raise AppError(400, "Не удалось прочитать XLSX-файл", "INVALID_XLSX") from error

    sheets: list[dict[str, Any]] = []
    for worksheet in workbook.worksheets:
        nonempty_rows: list[int] = []
        nonempty_cells = 0
        formulas = 0
        numeric_cells = 0
        date_cells = 0
        max_data_column = 0
        signatures: Counter[str] = Counter()
        for row in worksheet.iter_rows():
            kinds: list[str] = []
            row_has_data = False
            for cell in row:
                value = cell.value
                if value is None or value == "":
                    kinds.append("E")
                    continue
                row_has_data = True
                nonempty_cells += 1
                max_data_column = max(max_data_column, cell.column)
                if isinstance(value, str) and value.startswith("="):
                    formulas += 1
                    kinds.append("F")
                elif isinstance(value, (int, float)):
                    numeric_cells += 1
                    kinds.append("N")
                elif isinstance(value, (date, datetime)):
                    date_cells += 1
                    kinds.append("D")
                else:
                    kinds.append("T")
            if row_has_data:
                nonempty_rows.append(row[0].row)
                signatures["".join(kinds[:max_data_column])] += 1

        sample_rows = []
        for row_number in _sample_indexes(nonempty_rows):
            values = [safe_value(worksheet.cell(row_number, column).value) for column in range(1, max_data_column + 1)]
            sample_rows.append({"row": row_number, "values": values})

        sheets.append(
            {
                "name": worksheet.title,
                "min_row": min(nonempty_rows) if nonempty_rows else 0,
                "max_row": max(nonempty_rows) if nonempty_rows else 0,
                "max_column": max_data_column,
                "nonempty_rows": len(nonempty_rows),
                "nonempty_cells": nonempty_cells,
                "formulas": formulas,
                "numeric_cells": numeric_cells,
                "date_cells": date_cells,
                "merged_ranges": [str(item) for item in worksheet.merged_cells.ranges],
                "row_patterns": signatures.most_common(12),
                "sample_rows": sample_rows,
            }
        )
    workbook.close()
    return {"sheets": sheets, "sheet_count": len(sheets)}

