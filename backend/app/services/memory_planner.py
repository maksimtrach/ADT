"""Exact layout reuse with fresh bounds and full-file acceptance checks.

Only flat/general account-tree trial balances are supported in format v1.
This is deliberately not fuzzy semantic matching: unfamiliar layouts go to
the existing planner rather than inheriting another document's coordinates.
"""
from openpyxl import load_workbook

from backend.app.core.errors import AppError
from backend.app.models.domain import DataType
from backend.app.services.account_structure import MONEY_KEYS
from backend.app.services.document_memory import MemoryCard, MemoryStore
from backend.app.services.hypothesis_planner import SourceStructure
from backend.app.services.rule_planner import _merged_values, normalize_header
from backend.app.services.structure_recovery import StructureLayout, build_recovered_spec
from backend.app.services.table_regions import is_blank


def header_signature(sheet, start, end, merged=None):
    merged = _merged_values(sheet) if merged is None else merged
    rows = [[normalize_header(str(merged.get((r, c), sheet.cell(r, c).value) or ""))
             for c in range(1, min(sheet.max_column, 120) + 1)] for r in range(start, end + 1)]
    width = max((i + 1 for row in rows for i, cell in enumerate(row) if cell), default=0)
    return [row[:width] for row in rows]


def capture_card(path, spec, validation, processed_rows):
    start, end = spec.header_start_row or spec.header_row, spec.header_end_row or spec.header_row
    if (spec.hierarchy or spec.value_overrides or spec.row_rules.inherit_fields
            or not 1 <= end - start + 1 <= 5 or spec.data_start_row != end + 1
            or validation["status"] not in {"PASSED", "WARNINGS"} or validation["unprocessed_row_numbers"]
            or validation["row_balance"]["failed"] or not processed_rows
            or any(spec.column_mapping.get(key) is None for key in ("account", *MONEY_KEYS))):
        return None
    book = load_workbook(path, data_only=False, keep_links=False)
    try:
        sheet = book[spec.sheet_name]
        # A headerless explicit correction must not turn data into a template.
        if any(isinstance(c.value, (int, float)) or c.data_type == "f"
               for row in sheet.iter_rows(min_row=start, max_row=end, max_col=120) for c in row):
            return None
        signature = header_signature(sheet, start, end)
    finally:
        book.close()
    mapping = {k: v for k, v in spec.column_mapping.items() if v is not None}
    if (not signature or not signature[0] or max(mapping.values()) > len(signature[0])
            or any(len(cell) > 200 for row in signature for cell in row)
            or sum(len(cell) for row in signature for cell in row) > 16000):
        return None
    return MemoryCard(
        title="Общая ОСВ — " + ("дерево счетов" if spec.row_rules.account_tree else "плоская таблица"),
        description="Применять при точном совпадении нормализованной шапки и расположения колонок. "
                    "Границы данных и итоговая строка определяются заново; все данные проходят проверки. "
                    "Строки шапки могут смещаться. Перестановка колонок требует нового подхода.",
        header=signature, column_mapping=mapping,
        row_mode="account_tree" if spec.row_rules.account_tree else "flat",
        verified_rows=processed_rows,
        independent_totals=all(validation["control_comparisons"].get(k, {}).get("passed", False)
                               for k in MONEY_KEYS),
    )


def memory_plan(path, owner, template_id, trace):
    store = MemoryStore()
    records = [r for r in store.active(owner) if r.card.template_id == template_id]
    trace("MEMORY_SEARCH", {"active_cards": len(records), "matching": "exact_header", "schema_version": 1})
    if not records:
        return None
    source = SourceStructure(path)
    accepted = []
    matched_count = 0
    try:
        for sheet in source.formulas:
            merged = _merged_values(sheet)
            end = max((cell.row for row in sheet for cell in row if not is_blank(cell.value)), default=0)
            by_signature = {}
            for depth in {len(r.card.header) for r in records}:
                for first in range(1, min(100, end) - depth + 2):
                    band = header_signature(sheet, first, first + depth - 1, merged)
                    by_signature.setdefault(tuple(map(tuple, band)), []).append((first, first + depth - 1))
            for record in records:
                card = record.card
                for first, last in by_signature.get(tuple(map(tuple, card.header)), []):
                    matched_count += 1
                    if matched_count > 8:
                        trace("MEMORY_AMBIGUOUS", {"reason": "Слишком много совпадающих областей"})
                        return None
                    details = {"card_id": str(card.id), "revision": card.revision,
                               "sheet": sheet.title, "header": [first, last]}
                    try:
                        layout = StructureLayout(sheet_name=sheet.title, header_start_row=first,
                            header_end_row=last, data_start_row=last + 1, data_end_row=end,
                            column_mapping=card.column_mapping, row_mode=card.row_mode)
                        spec = build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, template_id)
                        spec.notes = [f"Подход из памяти {card.id}, версия {card.revision}; "
                                      "совпала шапка, диапазон определён заново, данные проверены"] + spec.notes[1:]
                        assessed = source.evaluate(spec, DataType.TRIAL_BALANCE, region_start=layout.data_start_row)
                        trace("MEMORY_CANDIDATE_CHECKED", {**details, **assessed.summary("memory")})
                        if assessed.eligible:
                            accepted.append((assessed, card))
                        else:
                            trace("MEMORY_REJECTED", {**details, "reason": "Не пройдены проверки нового файла"})
                            store.record_usage(card, False)
                    except AppError as error:
                        trace("MEMORY_REJECTED", {**details, "reason": error.message})
                        store.record_usage(card, False)
        interpretations = {(item.spec.sheet_name, item.spec.data_start_row,
                            tuple(sorted(item.spec.column_mapping.items())), item.spec.row_rules.account_tree)
                           for item, _ in accepted}
        if len(interpretations) > 1:
            trace("MEMORY_AMBIGUOUS", {"reason": "Несколько подтверждённых подходов дают разные результаты",
                                       "candidates": len(accepted)})
            return None
        if not accepted:
            return None
        assessed, card = accepted[0]
        store.record_usage(card, True)
        trace("MEMORY_APPLIED", {"card_id": str(card.id), "revision": card.revision,
                                 "spec": assessed.spec.model_dump(mode="json")})
        return assessed
    finally:
        source.close()
