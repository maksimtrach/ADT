from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from backend.app.models.domain import DataType, TransformationSpec
from backend.app.services.parser import ParseResult, parse_workbook
from backend.app.services.rule_planner import rule_candidates, _header_columns, _merged_values
from backend.app.services.validator import validate_result
from backend.app.services.account_structure import ACCOUNT_CODE, currency_code
from backend.app.services.table_regions import trailing_region

from backend.app.services.sheet_cache import SheetReadCache
from backend.app.services.checked_result import CheckedResult


@dataclass
class EvaluatedPlan:
    spec: TransformationSpec
    parsed: ParseResult
    validation: dict[str, Any]
    score: float
    evidence: dict[str, Any]
    eligible: bool
    hypothesis_number: int | None = None

    def __post_init__(self):
        self.checked_result = CheckedResult(self.spec, self.parsed, self.validation)

    def summary(self, candidate_id: str) -> dict[str, Any]:
        return {
            "candidate_id": candidate_id, "hypothesis_number": self.hypothesis_number,
            "sheet": self.spec.sheet_name,
            "header": [self.spec.header_start_row, self.spec.header_end_row],
            "data_rows": [self.spec.data_start_row, self.spec.data_end_row], "control_rows": self.spec.control_rows,
            "mapping": self.spec.column_mapping, "score": round(self.score, 3),
            "eligible": self.eligible, "evidence": self.evidence,
        }


@dataclass
class PlanSelection:
    candidates: list[EvaluatedPlan]
    confident: bool
    margin: float

    @property
    def best(self) -> EvaluatedPlan:
        return self.candidates[0]

    def summary(self) -> dict[str, Any]:
        return {
            "confident": self.confident, "margin": round(self.margin, 3),
            "candidates": [candidate.summary(str(i)) for i, candidate in enumerate(self.candidates)],
        }


class SourceStructure:
    """Formula and cached views of a workbook, shared by all candidate trials."""

    def __init__(self, path: Path):
        self.path = path
        self.formulas = load_workbook(path, data_only=False, keep_links=False)
        self.values = load_workbook(path, data_only=True, keep_links=False)

        self.sheet_caches = {}
        self.header_caches = {}
        self.formula_merges = {}
    def close(self):
        self.formulas.close()
        self.values.close()

    def columns(self, spec: TransformationSpec) -> dict[str, dict[str, Any]]:
        sheet = self.formulas[spec.sheet_name]
        cached = self.values[spec.sheet_name]
        if sheet.title not in self.formula_merges:
            self.formula_merges[sheet.title] = (sheet.max_column, _merged_values(sheet))
        width, merged = self.formula_merges[sheet.title]
        header_key = (sheet.title, spec.header_start_row or spec.header_row, spec.header_end_row or spec.header_row)
        if header_key not in self.header_caches:
            self.header_caches[header_key] = _header_columns(sheet, header_key[1], header_key[2], width, merged)[0]
        headers = self.header_caches[header_key]
        row_numbers = sorted(set(range(spec.data_start_row, spec.data_end_row + 1)) | set(spec.control_rows))
        result = {}
        for key, column in spec.column_mapping.items():
            if column is None:
                continue
            cells = [sheet.cell(row, column) for row in row_numbers]
            values = [cached.cell(cell.row, column).value for cell in cells]
            nonempty = [v for v in values if v not in (None, "")]
            formula_cells = [cell for cell in cells if cell.data_type == "f"]
            result[key] = {
                "column": get_column_letter(column), "header": headers[column - 1],
                "nonempty": len(nonempty), "distinct": len(set(map(str, nonempty))),
                "numeric_ratio": round(sum(isinstance(v, (int, float)) for v in nonempty) / max(1, len(nonempty)), 3),
                "formulas": len(formula_cells),
                "missing_formula_cache": sum(cached.cell(cell.row, column).value is None for cell in formula_cells),
                "sample_values": list(dict.fromkeys(str(v)[:100] for v in nonempty))[:5],
            }
        return result

    def evaluate(self, spec: TransformationSpec, data_type: DataType, *, region_start: int | None = None, preserve_bounds: bool = False) -> EvaluatedPlan:
        service_rows: list[int] = []
        if data_type == DataType.TRIAL_BALANCE and not spec.control_rows and not preserve_bounds and not spec.explicit_bounds:
            region = trailing_region(
                self.values[spec.sheet_name],
                spec.column_mapping,
                spec.data_start_row,
                spec.data_end_row,
            )
            if region.confident and (region.data_end != spec.data_end_row or region.control_rows):
                spec = spec.model_copy(deep=True)
                spec.data_end_row = region.data_end
                spec.control_rows = list(region.control_rows)
                if region.service_rows:
                    service_rows = list(region.service_rows)
                    spec.notes.append("Служебный футер исключён: строки " + ", ".join(map(str, region.service_rows)))
                if region.control_rows:
                    spec.notes.append("Итоговая строка выделена отдельно: " + ", ".join(map(str, region.control_rows)))
        if spec.sheet_name not in self.sheet_caches:
            self.sheet_caches[spec.sheet_name] = SheetReadCache(self.values[spec.sheet_name])
        parsed = parse_workbook(self.path, spec, data_type, workbook=self.values,
                                sheet_cache=self.sheet_caches[spec.sheet_name])
        validation = validate_result(parsed, spec)
        columns = self.columns(spec)
        required_keys = [column.key for column in spec.target_columns if column.required]
        identity_keys = [key for key in required_keys if "account" in key]
        identities = [row.get(key) for row in parsed.rows for key in identity_keys]
        identity_ratio = (
            sum(bool(ACCOUNT_CODE.fullmatch(str(row.get(key) or ""))
                and (not currency_code(row.get(key)) or (row.get("_row_kind") == "account" and row.get("account_name"))))
                for row in parsed.rows for key in identity_keys) / max(1, len(identities))
            if identity_keys else 1.0
        )
        suspicious = bool(
            len(parsed.rows) > 2 and identities
            and sum(value in {"0", "00", "000"} for value in identities) / len(identities) > 0.95
        )
        formula_missing = sum(item["missing_formula_cache"] for item in columns.values())
        numeric_keys = [column.key for column in spec.target_columns if column.kind == "number"]
        source_sheet = self.values[spec.sheet_name]
        numeric_columns = {spec.column_mapping[key] for key in numeric_keys if spec.column_mapping.get(key)}
        expected_rows = {
            row for row in range(region_start or spec.data_start_row, max([spec.data_end_row, *spec.control_rows]) + 1)
            if any(isinstance(source_sheet.cell(row, column).value, (int, float))
                   for column in numeric_columns)
        }
        accounted_rows = {row["_source_row"] for row in parsed.rows} | set(parsed.skipped_rows)
        omitted_rows = sorted(expected_rows - accounted_rows)
        numeric_coverage = sum(spec.column_mapping.get(key) is not None for key in numeric_keys) / max(1, len(numeric_keys))
        required_coverage = sum(spec.column_mapping.get(key) is not None for key in required_keys) / max(1, len(required_keys))
        checks = validation["control_comparisons"]
        totals_passed = bool(checks) and all(item["passed"] for item in checks.values())
        row_balance = validation["row_balance"]
        balance_ok = row_balance["checked"] > 0 and row_balance["failed"] == 0
        # Formula caches may be absent even when the formatted cells look filled.
        errors = len(parsed.numeric_errors) + sum(validation["missing_required"].values())
        error_free = bool(parsed.rows) and not errors and not formula_missing and not parsed.unprocessed_rows and not omitted_rows
        # Judge only the assigned name column; never search for a replacement.
        name_quality = None
        name_penalty = 0.0
        name_column = spec.column_mapping.get("account_name")
        if data_type == DataType.TRIAL_BALANCE and name_column:
            account_rows = [row for row in parsed.rows
                            if row.get("_row_kind") == "account"
                            or ("_row_kind" not in row and row.get("account"))]
            names = [source_sheet.cell(row["_source_row"], name_column).value for row in account_rows]
            filled = sum(value not in (None, "") for value in names)
            textual = sum(isinstance(value, str) and any(char.isalpha() for char in value)
                          for value in names)
            quality = textual / max(1, len(names))
            name_penalty = 10 * (1 - quality)
            name_quality = {"column": name_column, "checked": len(names), "nonempty": filled,
                            "textual": textual, "score": round(100 * quality, 2),
                            "penalty": round(name_penalty, 3)}

        score = (
            20 * identity_ratio + 20 * numeric_coverage + 10 * required_coverage
            + (20 if error_free else -30) + (10 if totals_passed else 0)
            + (10 if balance_ok else 0) + 5 * spec.confidence
            - (50 if suspicious else 0) - min(30, row_balance["failed"] * 2)
            - (30 if any(not item["passed"] for item in checks.values()) else 0)
            - name_penalty
        )
        eligible = (
            error_free and not suspicious and identity_ratio == 1 and required_coverage == 1
            and numeric_coverage == 1
            and not row_balance["failed"]
            and not any(not item["passed"] for item in checks.values())
            and (totals_passed or balance_ok or data_type == DataType.JOURNAL)
        )
        evidence = {
            "columns": columns, "rows": len(parsed.rows),
            "identity_ratio": round(identity_ratio, 3), "constant_zero_identity": suspicious,
            "account_name_quality": name_quality,
            "numeric_coverage": numeric_coverage, "required_coverage": required_coverage,
            "missing_formula_cache": formula_missing, "numeric_errors": len(parsed.numeric_errors),
            "missing_required": validation["missing_required"],
            "unprocessed_rows": len(parsed.unprocessed_rows),
            "row_coverage": {"source_numeric_rows": len(expected_rows), "omitted_rows": omitted_rows[:20]},
            "row_balance": row_balance, "independent_totals_passed": totals_passed,
            "duplicate_accounts": validation["duplicate_accounts"],
            "row_classification": validation["row_classification"],
            "standard_money_order": spec.row_rules.standard_money_order,
        }
        assessed = EvaluatedPlan(spec, parsed, validation, score, evidence, eligible)
        setattr(assessed, "service_rows", service_rows)
        return assessed


def choose_plan(path: Path, data_type: DataType, template_id: str, *,
                trace: Callable[[str, dict[str, Any]], None] | None = None) -> PlanSelection:
    if trace:
        trace("HYPOTHESES_GENERATION_STARTED", {})
    specs = rule_candidates(path, data_type, template_id)
    if trace:
        trace("HYPOTHESES_PREPARATION_STARTED", {"total": len(specs)})
    source = SourceStructure(path)
    try:
        regions = {}
        for spec in specs:
            regions.setdefault(spec.sheet_name, spec.data_start_row)
        candidates = []
        for number, spec in enumerate(specs, 1):
            if trace:
                trace("HYPOTHESIS_EVALUATION_STARTED", {
                    "hypothesis_number": number, "total": len(specs), "sheet": spec.sheet_name,
                })
            candidate = source.evaluate(spec, data_type, region_start=regions[spec.sheet_name])
            candidate.hypothesis_number = number
            candidates.append(candidate)
    finally:
        source.close()
    if trace:
        trace("HYPOTHESES_SELECTION_STARTED", {"total": len(specs)})
    candidates.sort(key=lambda item: item.score, reverse=True)
    best = candidates[0]
    # A different header band with the same extraction is not a competing interpretation.
    alternatives = [item for item in candidates[1:] if (
        item.spec.sheet_name, item.spec.data_start_row, item.spec.column_mapping, item.spec.hierarchy
    ) != (best.spec.sheet_name, best.spec.data_start_row, best.spec.column_mapping, best.spec.hierarchy)]
    margin = best.score - alternatives[0].score if alternatives else 100.0
    return PlanSelection(candidates, best.eligible and margin >= 5, margin)
