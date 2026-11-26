from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.core.errors import AppError
from backend.app.models.domain import (
    DataType,
    HierarchyLevel,
    HierarchyRule,
    RowRules,
    TargetColumn,
    TransformationSpec,
)
from backend.app.services.templates import get_template
from backend.app.services.account_structure import MONEY_KEYS, DERIVED_ACCOUNT_FIELDS


def standard_money_mapping(values):
    """Only three ordered, adjacent two-column period bands without side labels."""
    if any(re.search(r"\b(дт|кт|дебет\w*|кредит\w*)\b", value) for value in values):
        return None
    groups = [
        [i + 1 for i, v in enumerate(values) if "сальдо" in v and "нач" in v],
        [i + 1 for i, v in enumerate(values) if "оборот" in v],
        [i + 1 for i, v in enumerate(values) if "сальдо" in v and "кон" in v],
    ]
    if any(len(group) != 2 for group in groups):
        return None
    positions = [col for group in groups for col in group]
    if positions != list(range(positions[0], positions[0] + 6)):
        return None
    return dict(zip(MONEY_KEYS, positions))


ALIASES: dict[str, list[str]] = {
    "account": ["счет", "счёт", "счет учета", "номер счета", "account"],
    "subaccount": ["субсчет", "субсчёт", "суб счет"],
    "account_name": ["наименование счета", "наименование счёта", "название счета", "название счёта", "наименование"],
    "opening_debit": ["сальдо на начало дт", "сальдо на начало дебет", "начальное сальдо дебет", "нач. сальдо дт"],
    "opening_credit": ["сальдо на начало кт", "сальдо на начало кредит", "начальное сальдо кредит", "нач. сальдо кт"],
    "turnover_debit": ["оборот дт", "обороты дт", "обороты дебет", "дебетовый оборот"],
    "turnover_credit": ["оборот кт", "обороты кт", "обороты кредит", "кредитовый оборот"],
    "closing_debit": ["сальдо на конец дт", "сальдо на конец дебет", "конечное сальдо дебет", "кон. сальдо дт"],
    "closing_credit": ["сальдо на конец кт", "сальдо на конец кредит", "конечное сальдо кредит", "кон. сальдо кт"],
    "date": ["дата", "дата операции", "период", "date"],
    "document_number": ["номер документа", "№ документа", "документ", "номер"],
    "debit_account": ["счет дт", "счёт дт", "дебет", "дт счет"],
    "credit_account": ["счет кт", "счёт кт", "кредит", "кт счет"],
    "debit_analytics": ["аналитика дт", "субконто дт", "контрагент дт"],
    "credit_analytics": ["аналитика кт", "субконто кт", "контрагент кт"],
    "amount": ["сумма", "сумма операции", "оборот", "amount"],
    "description": ["содержание операции", "описание", "назначение", "комментарий"],
}

DIMENSION_ALIASES: dict[str, list[str]] = {
    "account": ALIASES["account"],
    "subaccount": ALIASES["subaccount"],
    "organization": ["организация", "компания", "юридическое лицо", "проверяемая организация"],
    "counterparty": ["контрагент", "поставщик", "покупатель"],
    "cost_item": ["статья затрат", "статья расходов", "вид расходов", "затраты"],
    "fixed_asset": ["основное средство", "основные средства", "объект ос"],
    "intangible_asset": ["нематериальный актив", "нематериальные активы", "нма"],
    "loan_agreement": [
        "кредитный договор",
        "кредитного договора",
        "договор кредита",
        "договора кредита",
        "договор займа",
        "договора займа",
    ],
    "contract": ["договор", "договор контрагента"],
    "project": ["проект", "направление деятельности"],
    "department": ["подразделение", "центр финансовой ответственности", "цфо"],
    "warehouse": ["склад", "место хранения"],
    "nomenclature": ["номенклатура", "товар", "материал"],
    "employee": ["сотрудник", "работник", "физическое лицо"],
    "cash_flow_item": ["статья движения денежных средств", "статья ддс"],
    "analytics": ["аналитика", "субконто"],
}

ACCOUNT_PATTERN = re.compile(r"^\d{1,3}(?:\.\d+)*$")


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[\n\r]+", " ", text)
    return re.sub(r"\s+", " ", text)


def alias_score(value: str, aliases: list[str]) -> float:
    return _alias_score(value, tuple(aliases))


@lru_cache(maxsize=16384)
def _alias_score(value: str, aliases: tuple[str, ...]) -> float:
    if not value:
        return 0
    best = 0.0
    for alias in aliases:
        candidate = normalize_header(alias)
        if value == candidate:
            return 1.0
        if candidate in value or value in candidate:
            best = max(best, 0.86)
        best = max(best, SequenceMatcher(None, value, candidate).ratio() * 0.78)
    return best


def _column_score(key: str, value: str, aliases: list[str], trial_balance: bool) -> float:
    if not trial_balance:
        return alias_score(value, aliases)
    if key in {"opening_debit", "opening_credit", "turnover_debit", "turnover_credit", "closing_debit", "closing_credit"}:
        tokens = set(re.findall(r"[а-яa-z]+", value))
        debit = bool(tokens & {"дт", "дебет", "дебетовый"})
        credit = bool(tokens & {"кт", "кредит", "кредитовый"})
        side = debit and not credit if key.endswith("debit") else credit and not debit
        period = ("нач" in value if key.startswith("opening") else
                  "кон" in value if key.startswith("closing") else "оборот" in value)
        return 1.0 if side and period else 0.0
    if key == "account" and "наименование" in value:
        return 0.0
    if key == "subaccount" and "субсчет" not in value and "суб счет" not in value:
        return 0.0
    return alias_score(value, aliases)


def _merged_values(worksheet) -> dict[tuple[int, int], Any]:
    values: dict[tuple[int, int], Any] = {}
    for merged in worksheet.merged_cells.ranges:
        value = worksheet.cell(merged.min_row, merged.min_col).value
        for row_number in range(merged.min_row, merged.max_row + 1):
            for column_number in range(merged.min_col, merged.max_col + 1):
                values[(row_number, column_number)] = value
    return values


def _account_cell_text(cell) -> str:
    value = cell.value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        number_format = str(cell.number_format or "")
        if re.fullmatch(r"0+", number_format):
            return str(int(value)).zfill(len(number_format))
    return str(value or "").strip()


def _column_has_data(worksheet, column: int, start_row: int, end_row: int) -> bool:
    return any(worksheet.cell(row, column).value not in (None, "") for row in range(start_row, end_row + 1))


def _code_tree_evidence(worksheet, account_column: int | None, start_row: int, end_row: int) -> bool:
    """Recognize a code-only account tree without treating a plain code list as a hierarchy."""
    if account_column is None:
        return False
    records: list[tuple[str, int]] = []
    for row_number in range(start_row, end_row + 1):
        cell = worksheet.cell(row_number, account_column)
        code = _account_cell_text(cell)
        if not ACCOUNT_PATTERN.fullmatch(code):
            continue
        visual_depth = max(int(cell.alignment.indent or 0), int(worksheet.row_dimensions[row_number].outlineLevel or 0))
        records.append((code, visual_depth))
    codes = {code for code, _ in records}
    parent_links = sum(
        any(".".join(code.split(".")[:level]) in codes for level in range(1, len(code.split("."))))
        for code, _ in records
        if "." in code
    )
    visual_children = sum(depth > 0 and "." in code for code, depth in records)
    # Formatting is strong evidence. Several explicit parent/child code pairs are
    # also sufficient because some exports discard outline/indent metadata.
    return len(codes) >= 3 and parent_links >= 1 and (visual_children >= 1 or parent_links >= 3)


def _unmerged_period_headers(worksheet, start_row, end_row, max_column, merged_values):
    """Extend a period label only over an explicit adjacent debit/credit pair."""
    inferred = {}

    def value(row, column):
        return merged_values.get((row, column), worksheet.cell(row, column).value)

    sides = {"дт": "debit", "дебет": "debit", "кт": "credit", "кредит": "credit"}
    for row in range(start_row + 1, end_row + 1):
        for column in range(1, max_column):
            left = sides.get(normalize_header(value(row, column)))
            right = sides.get(normalize_header(value(row, column + 1)))
            if {left, right} != {"debit", "credit"}:
                continue
            for parent_row in range(row - 1, start_row - 1, -1):
                title = normalize_header(value(parent_row, column))
                neighbor = normalize_header(value(parent_row, column + 1))
                if not title and not neighbor:
                    continue
                # Never cross another label, an actual merged range, or a spacer column.
                period = ("оборот" in title or
                          (("сальдо" in title or "остат" in title) and ("нач" in title or "кон" in title)))
                has_side = re.search(r"\b(дт|кт|дебет\w*|кредит\w*)\b", title)
                if period and not has_side and not neighbor and (parent_row, column + 1) not in merged_values:
                    inferred[parent_row, column + 1] = value(parent_row, column)
                break
    return inferred


def _header_columns(
    worksheet,
    start_row: int,
    end_row: int,
    max_column: int,
    merged_values: dict[tuple[int, int], Any],
) -> tuple[list[str], list[list[str]]]:
    composites: list[str] = []
    labels_by_column: list[list[str]] = []
    header_values = {**merged_values, **_unmerged_period_headers(
        worksheet, start_row, end_row, max_column, merged_values)}
    for column in range(1, max_column + 1):
        labels: list[str] = []
        seen: set[str] = set()
        for row_number in range(start_row, end_row + 1):
            value = header_values.get((row_number, column), worksheet.cell(row_number, column).value)
            if value in (None, ""):
                continue
            for part in re.split(r"[\n\r,;]+|\s+и\s+", str(value)):
                label = str(part).strip()
                normalized = normalize_header(label)
                if normalized and normalized not in seen:
                    labels.append(label)
                    seen.add(normalized)
        labels_by_column.append(labels)
        composites.append(normalize_header(" ".join(labels)))
    return composites, labels_by_column


def _dimension_key(label: str) -> str | None:
    normalized = normalize_header(label)
    best_key = None
    best_score = 0.0
    for key, aliases in DIMENSION_ALIASES.items():
        score = alias_score(normalized, aliases)
        if score > best_score:
            best_key = key
            best_score = score
    return best_key if best_score >= 0.72 else None


def _hierarchy_levels(labels: list[str], *, allow_unknown: bool) -> list[HierarchyLevel]:
    levels: list[HierarchyLevel] = []
    used: set[str] = set()
    unknown_index = 1
    for label in labels:
        key = _dimension_key(label)
        normalized = normalize_header(label)
        if key is None:
            if not allow_unknown or ACCOUNT_PATTERN.fullmatch(normalized) or not re.search(r"[a-zа-я]", normalized):
                continue
            while f"analytics_{unknown_index}" in used:
                unknown_index += 1
            key = f"analytics_{unknown_index}"
            unknown_index += 1
        if key in used:
            if key != "analytics":
                continue
            while f"analytics_{unknown_index}" in used:
                unknown_index += 1
            key = f"analytics_{unknown_index}"
            unknown_index += 1
        levels.append(HierarchyLevel(key=key, label=label, kind="account" if key == "account" else "analytic"))
        used.add(key)
    account = next((level for level in levels if level.key == "account"), None)
    if not account:
        return []
    return [account] + [level for level in levels if level.key != "account"]


def _target_columns(
    template_columns: list[TargetColumn],
    mapping: dict[str, int | None],
    hierarchy: HierarchyRule | None,
) -> tuple[list[TargetColumn], dict[str, int | None]]:
    if not hierarchy:
        return list(template_columns), mapping
    source_column = hierarchy.source_column
    account = next(column for column in template_columns if column.key == "account")
    text_columns = [account]
    used = {"account"}
    for column in template_columns:
        if column.kind == "number" or column.key in {"account", "account_name"}:
            continue
        text_columns.append(column)
        used.add(column.key)
    for column in template_columns:
        if (
            column.key == "account_name"
            and mapping.get(column.key) is not None
            and mapping[column.key] != source_column
        ):
            text_columns.append(column)
            used.add(column.key)
    for level in hierarchy.levels[1:]:
        if level.key not in used:
            text_columns.append(TargetColumn(key=level.key, label=level.label))
            used.add(level.key)
    columns = text_columns + [column for column in template_columns if column.kind == "number"]
    dynamic_mapping = {column.key: mapping.get(column.key) for column in columns}
    dynamic_mapping["account"] = source_column
    for level in hierarchy.levels[1:]:
        dynamic_mapping[level.key] = None
    return columns, dynamic_mapping


def rule_candidates(path: Path, data_type: DataType, template_id: str) -> list[TransformationSpec]:
    template = get_template(template_id)
    workbook = load_workbook(path, data_only=False, read_only=False, keep_links=False)
    candidates = []
    for worksheet in workbook.worksheets:
        max_scan_column = min(worksheet.max_column, 120)
        merged_values = _merged_values(worksheet)
        # Candidate bands contain text headers, not numeric data records.
        header_merges = [merged for merged in worksheet.merged_cells.ranges
                         if merged.min_row <= 100 and merged.min_col < merged.max_col
                         and merged.min_col <= max_scan_column]
        header_rows = set()
        for row in worksheet.iter_rows(max_row=min(worksheet.max_row, 100), max_col=max_scan_column):
            values = [cell.value for cell in row if cell.value not in (None, "")]
            numeric = sum(isinstance(value, (int, float)) or bool(ACCOUNT_PATTERN.fullmatch(str(value).strip())) for value in values)
            if values and not numeric:
                header_rows.add(row[0].row)
        actual_end = max((cell.row for row in worksheet for cell in row if cell.value not in (None, "")), default=0)
        for end_row in range(1, min(worksheet.max_row, 100) + 1):
            if end_row not in header_rows:
                continue
            for depth in range(1, min(5, end_row) + 1):
                start_row = end_row - depth + 1
                if any(row not in header_rows for row in range(start_row, end_row + 1)):
                    continue
                values, labels_by_column = _header_columns(
                    worksheet,
                    start_row,
                    end_row,
                    max_scan_column,
                    merged_values,
                )
                # A merged header is not evidence for its empty non-anchor cells.
                name_shadows = set()
                for merged in header_merges:
                    if not (start_row <= merged.min_row <= end_row) or merged.min_col == merged.max_col:
                        continue
                    if not _column_has_data(worksheet, merged.min_col, end_row + 1, actual_end):
                        continue
                    for column in range(merged.min_col + 1, min(merged.max_col, max_scan_column) + 1):
                        if (values[column - 1] == values[merged.min_col - 1]
                                and not _column_has_data(worksheet, column, end_row + 1, actual_end)):
                            name_shadows.add(column)
                mapping: dict[str, int | None] = {}
                scores: list[float] = []
                used: set[int] = set()
                for target in template.columns:
                    matches = [
                        (_column_score(target.key, value, ALIASES.get(target.key, [target.label]), data_type == DataType.TRIAL_BALANCE), -(index + 1))
                        for index, value in enumerate(values)
                        if index + 1 not in used
                        and not (target.key == "account_name" and index + 1 in name_shadows)
                    ]
                    score, negative_column = max(matches, default=(0.0, 0))
                    column = -negative_column
                    if score >= 0.55:
                        mapping[target.key] = column
                        scores.append(score)
                        used.add(column)
                    else:
                        mapping[target.key] = None
                # Some sources store the most detailed account code in a
                # separate subaccount column. It is an input helper, not a
                # target field; the parser coalesces it into "account".
                detail_matches = [
                    (_column_score("subaccount", value, ALIASES["subaccount"], True), -(index + 1))
                    for index, value in enumerate(values)
                    if index + 1 not in used
                ] if data_type == DataType.TRIAL_BALANCE else []
                detail_score, detail_negative_column = max(detail_matches, default=(0.0, 0))
                if detail_score >= 0.55:
                    mapping["_account_detail"] = -detail_negative_column
                matched = sum(value is not None for value in mapping.values())
                required_matched = sum(mapping[column.key] is not None for column in template.columns if column.required)
                total_required = sum(column.required for column in template.columns)
                aggregate = matched + required_matched * 1.5 + sum(scores) / max(len(scores), 1)
                if data_type == DataType.TRIAL_BALANCE and mapping.get("account"):
                    known_levels = _hierarchy_levels(labels_by_column[mapping["account"] - 1], allow_unknown=False)
                    aggregate += max(0, len(known_levels) - 1) * 0.35
                if total_required and required_matched == 0:
                    aggregate *= 0.25
                candidates.append((
                        aggregate,
                        worksheet.title,
                        start_row,
                        end_row,
                        mapping,
                        actual_end,
                        labels_by_column,
                        _code_tree_evidence(worksheet, mapping.get("account"), end_row + 1, actual_end),
                    ))
                standard = standard_money_mapping(values) if data_type == DataType.TRIAL_BALANCE else None
                if standard and mapping.get("account") and not any(mapping.get(key) for key in MONEY_KEYS):
                    candidates.append((aggregate + 6, worksheet.title, start_row, end_row,
                                       {**mapping, **standard}, actual_end, labels_by_column,
                                       _code_tree_evidence(worksheet, mapping.get("account"), end_row + 1, actual_end)))
                # Retain near-equal column assignments instead of settling ties
                # by physical column order. Data checks decide between them.
                for target in template.columns:
                    current = mapping.get(target.key)
                    if current is None:
                        continue
                    current_score = _column_score(target.key, values[current - 1], ALIASES.get(target.key, [target.label]), data_type == DataType.TRIAL_BALANCE)
                    for index, value in enumerate(values, 1):
                        if index == current or index in mapping.values():
                            continue
                        if target.key == "account_name" and index in name_shadows:
                            continue
                        score = _column_score(target.key, value, ALIASES.get(target.key, [target.label]), data_type == DataType.TRIAL_BALANCE)
                        if score >= max(0.55, current_score - 0.12):
                            if (target.kind == "number"
                                    and _column_has_data(worksheet, current, end_row + 1, actual_end)
                                    and not _column_has_data(worksheet, index, end_row + 1, actual_end)):
                                continue
                            alternative = {**mapping, target.key: index}
                            candidates.append((aggregate + score - current_score, worksheet.title,
                                               start_row, end_row, alternative, actual_end, labels_by_column,
                                               _code_tree_evidence(worksheet, alternative.get("account"), end_row + 1, actual_end)))
    workbook.close()
    if not candidates or max(item[0] for item in candidates) < 1:
        raise AppError(422, "Не удалось определить строку заголовков", "STRUCTURE_NOT_RECOGNIZED")
    selected = []
    signatures = set()
    for candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
        _, sheet, start, end, mapping, _, _, _ = candidate
        signature = (sheet, end, tuple(mapping.items()))
        if signature in signatures:
            continue
        signatures.add(signature)
        selected.append(_build_spec(candidate, data_type, template_id))
        if len(selected) == 7:
            break
    return selected


def _build_spec(candidate, data_type: DataType, template_id: str) -> TransformationSpec:
    template = get_template(template_id)
    _, sheet_name, header_start_row, header_end_row, mapping, data_end_row, labels_by_column, code_tree = candidate
    hierarchy = None
    account_name_column = None
    if data_type == DataType.TRIAL_BALANCE and mapping.get("account"):
        source_column = mapping["account"]
        source_labels = labels_by_column[source_column - 1]
        # "Счет / Код" describes one field, not two analytic levels.
        code_header = any(normalize_header(label) == "код" for label in source_labels)
        proposed_name_column = mapping.get("account_name")
        if proposed_name_column is not None:
            name_labels = labels_by_column[proposed_name_column - 1]
            if any("наименование" in normalize_header(label)
                   and (code_header or "счет" in normalize_header(label)
                        or (proposed_name_column == source_column + 1
                            and normalize_header(label) == "наименование")) for label in name_labels):
                account_name_column = proposed_name_column
        levels = [] if code_header or account_name_column else _hierarchy_levels(source_labels, allow_unknown=True)
        if len(levels) > 1:
            hierarchy = HierarchyRule(source_column=source_column, levels=levels)
    account_detail_column = mapping.get("_account_detail")
    target_columns, mapping = _target_columns(template.columns, mapping, hierarchy)
    account_tree = account_name_column is not None or (code_tree and hierarchy is None)
    if account_tree:
        target_columns = list(template.columns)
        mapping = {column.key: mapping.get(column.key) for column in target_columns}
        if account_detail_column is not None:
            mapping["_account_detail"] = account_detail_column
    matched = sum(mapping.get(column.key) is not None for column in target_columns)
    confidence = min(0.98, matched / max(len(target_columns), 1) + 0.18)
    inherit = [] if account_tree else [level.key for level in hierarchy.levels] if hierarchy else (
        ["account", "account_name"] if data_type == DataType.TRIAL_BALANCE else []
    )
    notes = [
        f"Шапка определена в строках {header_start_row}–{header_end_row}",
        f"Автоматически сопоставлено полей: {matched} из {len(target_columns)}",
    ]
    if hierarchy:
        notes.append("Иерархия аналитик: " + " → ".join(level.label for level in hierarchy.levels))
    if account_tree and account_name_column is None:
        notes.append("Иерархия счетов определена по кодам, отступам и уровням строк; наименование счёта в источнике отсутствует")
    derived = DERIVED_ACCOUNT_FIELDS if account_tree else set()
    standard = standard_money_mapping([normalize_header(" ".join(labels)) for labels in labels_by_column])
    assumed_order = bool(standard and all(mapping.get(key) == value for key, value in standard.items()))
    if assumed_order:
        notes.append("Применён стандартный порядок ОСВ: Дт/Кт начала, Дт/Кт оборотов, Дт/Кт конца; подписи сторон отсутствуют, арифметика не доказывает их порядок")
    if account_tree:
        confidence_keys = {"account", *MONEY_KEYS}
        if account_name_column is not None:
            confidence_keys.add("account_name")
        confidence = min(0.98, sum(mapping.get(key) is not None for key in confidence_keys) / len(confidence_keys))
        notes.append("Счета и субсчета сохранены; итоговые суммы рассчитываются по верхнему уровню без повторного сложения дочерних счетов")
    missing = [column.label for column in target_columns if mapping[column.key] is None and column.key not in inherit and column.key not in derived]
    if missing:
        notes.append("Не найдены поля: " + ", ".join(missing))
    return TransformationSpec(
        sheet_name=sheet_name,
        header_row=header_end_row,
        header_start_row=header_start_row,
        header_end_row=header_end_row,
        data_start_row=header_end_row + 1,
        data_end_row=data_end_row,
        column_mapping=mapping,
        target_columns=target_columns,
        row_rules=RowRules(inherit_fields=inherit, account_tree=account_tree,
                           classify_account_rows=account_name_column is not None, standard_money_order=assumed_order),
        hierarchy=hierarchy,
        confidence=confidence,
        notes=notes,
    )


def rule_plan(path: Path, data_type: DataType, template_id: str) -> TransformationSpec:
    # Public entry point remains compatible; selection now includes a trial parse.
    from backend.app.services.hypothesis_planner import choose_plan
    return choose_plan(path, data_type, template_id).best.spec
