"""Human-readable, persisted description of a recognized source layout."""
from __future__ import annotations

from typing import Any, Literal

from openpyxl.utils import get_column_letter

from backend.app.models.domain import TargetColumn, TransformationSpec


RecognitionSource = Literal["RULES", "LLM", "USER", "MEMORY"]


def structure_report(
    *,
    target_columns: list[TargetColumn],
    column_mapping: dict[str, int | None],
    sheet_name: str,
    header_start_row: int | None,
    header_end_row: int | None,
    data_start_row: int | None,
    data_end_row: int | None,
    control_rows: list[int] | tuple[int, ...] = (),
    service_rows: list[int] | tuple[int, ...] = (),
    field_sources: dict[str, RecognitionSource] | None = None,
    default_source: RecognitionSource = "RULES",
    header_source: RecognitionSource | None = None,
    footer_source: RecognitionSource | None = None,
) -> dict[str, Any]:
    sources = field_sources or {}
    fields = []
    for target in target_columns:
        column = column_mapping.get(target.key)
        fields.append(
            {
                "key": target.key,
                "label": target.label,
                "column": column,
                "column_letter": get_column_letter(column) if column is not None else None,
                "source": sources.get(target.key, default_source) if column is not None else None,
            }
        )
    return {
        "sheet_name": sheet_name,
        "header_start_row": header_start_row,
        "header_end_row": header_end_row,
        "data_start_row": data_start_row,
        "data_end_row": data_end_row,
        "control_rows": list(control_rows),
        "service_rows": list(service_rows),
        "header_source": header_source or default_source,
        "footer_source": footer_source,
        "fields": fields,
    }


def report_from_spec(
    spec: TransformationSpec,
    *,
    field_sources: dict[str, RecognitionSource] | None = None,
    default_source: RecognitionSource = "RULES",
    header_source: RecognitionSource | None = None,
    footer_source: RecognitionSource | None = None,
    service_rows: list[int] | tuple[int, ...] = (),
) -> dict[str, Any]:
    return structure_report(
        target_columns=spec.target_columns,
        column_mapping=spec.column_mapping,
        sheet_name=spec.sheet_name,
        header_start_row=None if spec.header_absent else spec.header_start_row or spec.header_row,
        header_end_row=None if spec.header_absent else spec.header_end_row or spec.header_row,
        data_start_row=spec.data_start_row,
        data_end_row=spec.data_end_row,
        control_rows=spec.control_rows,
        service_rows=service_rows,
        field_sources=field_sources,
        default_source=default_source,
        header_source=header_source,
        footer_source=footer_source,
    )


__all__ = ["RecognitionSource", "report_from_spec", "structure_report"]
