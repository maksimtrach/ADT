from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.app.models.domain import DataType
from backend.app.services.hypothesis_planner import choose_plan
from backend.app.services.rule_planner import _header_columns, _merged_values


@pytest.mark.parametrize("debit,credit", [("Дебет", "Кредит"), ("Дт", "Кт"), ("Кредит", "Дебет")])
@pytest.mark.parametrize("depth", [2, 3])
def test_period_inherited_only_inside_explicit_pair(debit, credit, depth):
    book = Workbook()
    sheet = book.active
    sheet.append(["Счет", "Наименование счета", None, "Сальдо на начало периода", None, None,
                  "Обороты за период", None, "Сальдо на конец периода", None])
    for col, label in [(4, debit), (5, credit), (7, debit), (8, credit), (9, debit), (10, credit)]:
        sheet.cell(depth, col, label)
    headers, _ = _header_columns(sheet, 1, depth, 10, {})
    assert headers[4] == "сальдо на начало периода " + credit.lower()
    assert headers[7] == "обороты за период " + credit.lower()
    assert headers[9] == "сальдо на конец периода " + credit.lower()
    assert headers[2] == headers[5] == ""
    assert sheet["E1"].value is None  # Interpretation must not mutate the workbook.
    book.close()


@pytest.mark.parametrize("parent,child,expected", [
    (["Сальдо на начало", "Сальдо на конец"], ["Дебет", "Кредит"], "сальдо на конец кредит"),
    (["Сальдо на начало", None], ["Дебет", "Дебет"], "дебет"),
    (["Сальдо на начало", None], ["Дебет", "Сумма"], "сумма"),
    (["Сальдо на начало Дт", None], ["Дебет", "Кредит"], "кредит"),
    (["Организация", None], ["Дебет", "Кредит"], "кредит"),
    (["Сальдо на начало", None, None], ["Дебет", None, "Кредит"], "кредит"),
    ([None, "Обороты"], ["Кредит", "Дебет"], "обороты дебет"),
])
def test_does_not_fill_unproven_groups(parent, child, expected):
    book = Workbook()
    sheet = book.active
    sheet.append(parent)
    sheet.append(child)
    headers, _ = _header_columns(sheet, 1, 2, len(child), {})
    assert headers[-1] == expected
    if parent[0] is None:
        assert headers[0] == "кредит"  # No backwards fill across period boundary.
    book.close()


def test_existing_merged_header_is_preserved():
    book = Workbook()
    sheet = book.active
    sheet.append(["Обороты", None])
    sheet.append(["Дебет", "Кредит"])
    sheet.merge_cells("A1:B1")
    headers, _ = _header_columns(sheet, 1, 2, 2, _merged_values(sheet))
    assert headers == ["обороты дебет", "обороты кредит"]
    book.close()


def test_unmerged_workbook_with_spacers_and_account_after_total(tmp_path):
    path = tmp_path / "unmerged.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Счет", "Наименование счета", None, "Сальдо на начало периода", None, None,
                  "Обороты за период", None, "Сальдо на конец периода", None])
    sheet.append([None, None, None, "Дебет", "Кредит", None, "Дебет", "Кредит", "Дебет", "Кредит"])
    sheet.append([1, "Основные средства", None, 100, None, None, 20, 10, 110, None])
    sheet["A3"].number_format = "00"
    sheet.append(["01.01", "Объекты", None, 100, None, None, 20, 10, 110, None])
    sheet.append([2, "Амортизация", None, None, 100, None, 10, 20, None, 110])
    sheet["A5"].number_format = "00"
    sheet.append(["Итого", None, None, 100, 100, None, 30, 30, 110, 110])
    sheet.append([1, "Арендованные основные средства", None, 25, None, None, None, None, 25, None])
    sheet["A7"].number_format = "000"
    book.save(path)
    book.close()
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    result = selection.best
    assert result.eligible
    assert len(result.parsed.rows) == 4
    assert result.parsed.rows[-1]["account"] == "001"
    assert result.validation["control_rows"] == [6]
    assert result.validation["scope_excluded_rows"] == [7]
    assert all(c["passed"] for c in result.validation["control_comparisons"].values())
    assert not result.spec.row_rules.standard_money_order
    assert [result.spec.column_mapping[k] for k in ("opening_debit", "opening_credit", "turnover_debit",
            "turnover_credit", "closing_debit", "closing_credit")] == [4, 5, 7, 8, 9, 10]


def test_real_fifth_workbook_is_confident_without_model():
    path = Path("test-data/complex/Общая ОСВ_тест_5.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    result = selection.best
    assert len(result.parsed.rows) == 57
    assert result.parsed.rows[-1]["account"] == "001"
    assert result.validation["control_rows"] == [64]
    assert result.validation["scope_excluded_rows"] == [65]
    assert all(c["passed"] for c in result.validation["control_comparisons"].values())
    assert result.validation["row_balance"]["failed"] == 0
