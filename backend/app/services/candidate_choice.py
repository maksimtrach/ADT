"""Focused evidence for choosing between already validated interpretations."""
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from backend.app.services.file_profiler import safe_value
from backend.app.services.llm_context import llm_candidate_summary, sheet_context


def _partition(records):
    """Move identical values to shared context; preserve each candidate's differences."""
    keys = list(dict.fromkeys(key for record in records for key in record))
    common = {key: records[0][key] for key in keys
              if all(key in record and record[key] == records[0].get(key) for record in records)}
    return common, [{key: value for key, value in record.items() if key not in common}
                    for record in records]


def _source_windows(path, specs, differing_fields, layout_differences):
    values = load_workbook(path, data_only=True, keep_links=False)
    try:
        formulas = load_workbook(path, data_only=False, keep_links=False)
        try:
            sheets = []
            for name in dict.fromkeys(spec.sheet_name for spec in specs):
                local = [spec for spec in specs if spec.sheet_name == name]
                headers = sorted({(spec.header_start_row or spec.header_row,
                                   spec.header_end_row or spec.header_row) for spec in local})
                context = sheet_context(values[name], formulas[name], headers)
                # A boundary dispute needs actual nearby rows, not just header samples.
                boundary_rows = set()
                for spec in local:
                    anchors = []
                    if "data_rows" in layout_differences:
                        anchors.extend([spec.data_start_row, spec.data_end_row])
                    if "control_rows" in layout_differences:
                        anchors.extend(spec.control_rows)
                    for anchor in anchors:
                        boundary_rows.update(range(max(1, anchor - 2), min(context["max_row"], anchor + 2) + 1))
                existing = {row["row"] for row in context["samples"]}
                for row in sorted(boundary_rows - existing):
                    context["samples"].append({
                        "row": row,
                        "cells": {cell.coordinate: safe_value(cell.value)
                                  for cell in values[name][row][:120] if cell.value is not None},
                        "formula_columns": [cell.column for cell in formulas[name][row][:120]
                                            if cell.data_type == "f"],
                    })
                if boundary_rows:
                    context["boundary_rows"] = sorted(boundary_rows)
                    context["selection"] = "header_plus_3_before_5_after_and_disputed_boundaries"
                    context["merged_ranges"] = sorted({*context["merged_ranges"], *(
                        str(merged) for merged in formulas[name].merged_cells.ranges
                        if any(merged.min_row <= row <= merged.max_row for row in boundary_rows))})
                # Retain all columns for layout disputes; otherwise focus on disputed fields.
                columns = {spec.column_mapping[key] for spec in local for key in differing_fields
                           if spec.column_mapping.get(key)} if not layout_differences else set()
                context["samples"].sort(key=lambda row: row["row"])
                for row in context["samples"]:
                    if columns:
                        row["cells"] = {coord: value for coord, value in row["cells"].items()
                                        if column_index_from_string("".join(c for c in coord if c.isalpha())) in columns}
                        row["formula_columns"] = [column for column in row["formula_columns"] if column in columns]
                    if not any(row.get("indents", [])):
                        row.pop("indents", None)
                    if not row.get("outline_level"):
                        row.pop("outline_level", None)
                sheets.append(context)
            return {"sheets": sheets, "sheet_count": len(sheets)}
        finally:
            formulas.close()
    finally:
        values.close()


def candidate_request(selection, source_path=None):
    eligible = [(index, item) for index, item in enumerate(selection.candidates) if item.eligible]
    if not eligible:
        return None
    specs = [item.spec for _, item in eligible]
    common_mapping, mappings = _partition([spec.column_mapping for spec in specs])
    differing_fields = list(dict.fromkeys(key for mapping in mappings for key in mapping))
    layouts = [{"sheet": spec.sheet_name,
                "header": [spec.header_start_row or spec.header_row, spec.header_end_row or spec.header_row],
                "data_rows": [spec.data_start_row, spec.data_end_row], "control_rows": spec.control_rows,
                "hierarchy": spec.hierarchy.model_dump(mode="json") if spec.hierarchy else None,
                "row_rules": spec.row_rules.model_dump(mode="json")} for spec in specs]
    common_layout, layouts = _partition(layouts)
    layout_differences = list(dict.fromkeys(key for layout in layouts for key in layout))
    # Identical hierarchy/rules do not help resolve a column dispute.
    common_layout.pop("hierarchy", None)
    common_layout.pop("row_rules", None)
    summaries = [llm_candidate_summary(item, index) for index, item in eligible]
    checks = [{key: value for key, value in summary["evidence"].items()
               if key in {"rows", "identity_ratio", "row_balance", "independent_totals_passed",
                          "missing_required", "numeric_errors", "row_coverage", "account_name_quality"}}
              for summary in summaries]
    common_checks, checks = _partition(checks)
    candidates = []
    for (index, _), mapping, layout, summary, check in zip(eligible, mappings, layouts, summaries, checks):
        column_evidence = {key: summary["evidence"].get("columns", {}).get(key, {}) for key in differing_fields}
        candidates.append({"candidate_id": index, **layout, "mapping": mapping,
                           "columns": column_evidence, "checks": check})
    labels = {column.key: column.label for spec in specs for column in spec.target_columns}
    questions = [f"Из какой колонки читать поле «{labels.get(key, key)}» ({key})?" for key in differing_fields]
    if layout_differences:
        questions.append("Какие из предложенных границ, листов или правил строк подтверждаются исходными ячейками?")
    return {
        "task": "Выбери один готовый вариант по спорным полям и структуре. Не создавай новую схему.",
        "questions": questions,
        "instructions": [
            "Для наименований выбирай колонку с фактическими названиями, а не пустую часть объединённых ячеек.",
            "Для остальных полей сопоставляй заголовок, заполненность и фактический тип значений.",
            "Не выбирай только по расположению слева/справа или номеру варианта.",
            "Все варианты прошли базовые проверки; это не доказывает правильность каждого поля.",
            "Если доказательств недостаточно или несколько вариантов одинаково правдоподобны, верни candidate_id=null.",
            "Иначе верни числовой candidate_id выбранного варианта и короткую причину reason, согласованную с выбором.",
        ],
        "common": {**common_layout, "mapping": common_mapping, "checks": common_checks},
        "candidates": candidates,
        "profile": _source_windows(source_path, specs, differing_fields, layout_differences) if source_path else None,
    }
