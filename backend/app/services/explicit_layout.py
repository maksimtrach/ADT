"""A complete user mapping can define a table even if its header is missing."""
from backend.app.core.errors import AppError
from backend.app.models.domain import RowRules, TransformationSpec
from backend.app.services.account_structure import ACCOUNT_CODE, MONEY_KEYS, account_columns
from backend.app.services.hypothesis_planner import SourceStructure, PlanSelection
from backend.app.services.table_regions import is_blank, trailing_control
from backend.app.services.templates import get_template


def complete_mapping(mapping):
    return bool(mapping and all(mapping.get(k) for k in ("account", "account_name", *MONEY_KEYS)))


def plan_from_explicit_mapping(path, data_type, template_id, mapping):
    source = SourceStructure(path)
    try:
        candidates = []
        for sheet in source.values:
            if max(mapping.values()) > sheet.max_column:
                continue
            end = max((c.row for row in source.formulas[sheet.title] for c in row if not is_blank(c.value)), default=0)
            start = next((r for r in range(1, end + 1)
                          if ACCOUNT_CODE.fullmatch(str(sheet.cell(r, mapping["account"]).value or "").strip())
                          and not is_blank(sheet.cell(r, mapping["account_name"]).value)
                          and any(isinstance(sheet.cell(r, mapping[k]).value, (int, float)) for k in MONEY_KEYS)), None)
            if start is None:
                continue
            control = trailing_control(sheet, mapping, start, end)
            columns = account_columns(get_template(template_id).columns)
            spec = TransformationSpec(
                sheet_name=sheet.title, header_row=max(1, start - 1),
                header_start_row=1 if start > 1 else None, header_end_row=start - 1 if start > 1 else None,
                data_start_row=start, data_end_row=end - 1 if control else end,
                control_rows=[control] if control else [],
                column_mapping={c.key: mapping.get(c.key) for c in columns}, target_columns=columns,
                row_rules=RowRules(account_tree=True, classify_account_rows=True), confidence=0.98,
                notes=["Колонки заданы явно пользователем; шапка не требуется для сопоставления полей",
                       f"Данные: строки {start}–{end - 1 if control else end}"]
                      + ([f"Контрольная строка: {control}"] if control else []),
            )
            candidates.append(source.evaluate(spec, data_type, region_start=1))
        if not candidates:
            raise AppError(422, "В указанных колонках не найдена таблица счетов с числовыми суммами", "INVALID_COLUMN_MAPPING")
        if len(candidates) != 1:
            raise AppError(422, "Указанным колонкам соответствуют несколько листов; требуется выбор листа", "STRUCTURE_AMBIGUOUS")
        return PlanSelection(candidates, candidates[0].eligible, 100)
    finally:
        source.close()
