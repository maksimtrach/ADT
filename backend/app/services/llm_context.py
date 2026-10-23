"""Small, coordinate-preserving source windows for LLM requests."""
from copy import deepcopy

from openpyxl import load_workbook

from backend.app.services.file_profiler import safe_value
from backend.app.services.table_regions import is_blank


def context_rows(end, headers):
    if not headers:
        return list(range(1, min(end, 20) + 1))
    return sorted({row for start, stop in headers
                   for row in range(max(1, start - 3), min(end, stop + 5) + 1)})


def sheet_context(values, formulas, headers):
    end = max((c.row for row in formulas for c in row if not is_blank(c.value)), default=0)
    selected = context_rows(end, headers)
    samples = []
    for row in selected:
        samples.append({
            "row": row,
            "cells": {c.coordinate: safe_value(c.value) for c in values[row][:120] if c.value is not None},
            "formula_columns": [c.column for c in formulas[row][:120] if c.data_type == "f"],
            "indents": [int(c.alignment.indent or 0) for c in formulas[row][:120]],
            "outline_level": int(formulas.row_dimensions[row].outlineLevel or 0),
        })
    return {
        "name": values.title, "max_row": end, "max_column": values.max_column,
        "header_ranges": [list(h) for h in headers],
        "selection": "header_plus_3_before_5_after" if headers else "no_header_first_20_rows",
        "merged_ranges": [str(r) for r in formulas.merged_cells.ranges
                          if any(r.min_row <= row <= r.max_row for row in selected)],
        "samples": samples,
    }


def source_context(path, specs):
    values = load_workbook(path, data_only=True, keep_links=False)
    try:
        formulas = load_workbook(path, data_only=False, keep_links=False)
        try:
            sheets = []
            for name in dict.fromkeys(spec.sheet_name for spec in specs):
                headers = sorted({(s.header_start_row or s.header_row, s.header_end_row or s.header_row)
                                  for s in specs if s.sheet_name == name})
                sheets.append(sheet_context(values[name], formulas[name], headers))
            return {"sheets": sheets, "sheet_count": len(sheets)}
        finally:
            formulas.close()
    finally:
        values.close()


def llm_candidate_summary(candidate, candidate_id):
    summary = deepcopy(candidate.summary(candidate_id))
    # Full-file statistics remain useful, but raw examples must come only from the window.
    for column in summary["evidence"].get("columns", {}).values():
        column.pop("sample_values", None)
    return summary
