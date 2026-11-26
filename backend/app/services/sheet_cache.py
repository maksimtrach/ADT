"""Read-only, per-workbook data reused while testing different hypotheses."""
from backend.app.services.table_regions import is_blank


class SheetReadCache:
    def __init__(self, worksheet):
        self.worksheet = worksheet
        self.max_column = worksheet.max_column
        self.merged_values = {}
        for merged in worksheet.merged_cells.ranges:
            cell = worksheet.cell(merged.min_row, merged.min_col)
            for row in range(merged.min_row, merged.max_row + 1):
                for column in range(merged.min_col, merged.max_col + 1):
                    self.merged_values[row, column] = (cell.value, cell.coordinate)
        self._rows = {}

    def row(self, number):
        if number not in self._rows:
            values = tuple(self.worksheet.cell(number, col).value for col in range(1, self.max_column + 1))
            self._rows[number] = (
                values,
                all(is_blank(value) for value in values),
                " ".join(str(value).strip().lower() for value in values[:12] if value not in (None, "")),
            )
        return self._rows[number]
