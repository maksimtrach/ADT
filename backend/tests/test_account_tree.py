from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from backend.app.models.domain import DataType
from backend.app.services.exporter import export_result
from backend.app.services.parser import normalize_number, parse_workbook
from backend.app.services.pipeline import baseline_is_reliable
from backend.app.services.rule_planner import rule_plan
from backend.app.services.validator import validate_result


@pytest.fixture
def account_tree_file(tmp_path):
    path = tmp_path / "two-level.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "TB"
    sheet.append([None, "Оборотно-сальдовая ведомость"])
    sheet.append([None, "Счет", None, None, "Сальдо на начало периода", None, "Оборот за период", None, "Сальдо на конец периода"])
    sheet.append([None, "Код", "Наименование", None, "Дебет", "Кредит", "Дебет", "Кредит", "Дебет", "Кредит"])
    for area in ("B2:D2", "C3:D3", "E2:F2", "G2:H2", "I2:J2"):
        sheet.merge_cells(area)
    sheet.append([None, 1, "Сводный счёт", None, 100, 20, 30, 10, 120, 20])
    sheet["B4"].number_format = "00"
    sheet.append([None, "01.01", "Детализация", None, 130, 50, 30, 10, 150, 50])
    sheet.append([None, 0, "Вспомогательный счёт", None, 0.01, None, None, None, 0.01])
    sheet["B6"].number_format = "000"
    sheet.append([None, None, None, None, 100.01, 20, 30, 10, 120.01, 20])
    for row in range(4, 8):
        sheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
    # Formatted trailing blank row must not hide the actual total.
    sheet.cell(8, 10).number_format = "0.00"
    book.save(path)
    book.close()
    return path


def test_multilevel_header_account_tree_and_unlabelled_totals(account_tree_file, tmp_path):
    spec = rule_plan(account_tree_file, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert (spec.header_start_row, spec.header_end_row, spec.data_start_row, spec.data_end_row) == (2, 3, 4, 7)
    assert spec.hierarchy is None
    assert spec.row_rules.account_tree
    assert spec.column_mapping["account"] == 2
    assert spec.column_mapping["account_name"] == 3
    assert [spec.column_mapping[key] for key in (
        "opening_debit", "opening_credit", "turnover_debit", "turnover_credit", "closing_debit", "closing_credit",
    )] == [5, 6, 7, 8, 9, 10]
    parsed = parse_workbook(account_tree_file, spec, DataType.TRIAL_BALANCE)
    assert [row["account"] for row in parsed.rows] == ["01", "01.01", "000"]
    assert parsed.rows[1]["parent_account"] == "01"
    assert parsed.rows[1]["account_level"] == "2"
    assert parsed.rows[0]["row_type"] == "Сводный счёт"
    assert 7 in parsed.skipped_rows
    assert not parsed.numeric_errors
    validation = validate_result(parsed, spec)
    assert validation["status"] == "PASSED"
    assert validation["control_row_count"] == 2
    assert validation["calculated_sums"]["opening_debit"] == 100.01
    assert len(validation["control_comparisons"]) == 6
    assert baseline_is_reliable(spec, validation)
    output = tmp_path / "result.xlsx"
    export_result(output, parsed.rows, spec, validation)
    book = load_workbook(output)
    assert book["Результат"]["A2"].value == "01"
    assert book["Результат"].max_row == 4
    assert any(row[0] == "Способ суммирования" for row in book["Контроль"].values)
    book.close()


def test_mismatched_totals_prevent_skipping_llm(account_tree_file):
    book = load_workbook(account_tree_file)
    book.active["E7"] = 999
    book.save(account_tree_file)
    book.close()
    spec = rule_plan(account_tree_file, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    parsed = parse_workbook(account_tree_file, spec, DataType.TRIAL_BALANCE)
    validation = validate_result(parsed, spec)
    assert validation["status"] == "FAILED"
    assert not baseline_is_reliable(spec, validation)


def test_text_with_digits_is_not_silently_parsed_as_money():
    assert normalize_number("Основные средства на счете 01") == (None, True)


def test_user_two_level_workbook_regression():
    path = Path("test-data/complex/TB_2_lev_header.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    spec = rule_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    parsed = parse_workbook(path, spec, DataType.TRIAL_BALANCE)
    validation = validate_result(parsed, spec)
    assert (spec.header_start_row, spec.header_end_row) == (6, 7)
    assert len(parsed.rows) == 129
    assert not parsed.numeric_errors
    assert not parsed.unprocessed_rows
    assert parsed.rows[0]["account"] == "01"
    assert parsed.rows[-1]["account"] == "000"
    assert validation["status"] == "PASSED"
    assert validation["control_row_count"] == 34
    assert len(validation["control_comparisons"]) == 6
    assert all(item["difference"] == 0 for item in validation["control_comparisons"].values())
    assert baseline_is_reliable(spec, validation)
