from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

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
    assert parsed.rows[1]["parent_source_row"] == "4"
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
    result_sheet = book["Результат"]
    assert result_sheet["A2"].value == "01"
    headers = [cell.value for cell in result_sheet[1]]
    assert headers[-5:] == [
        "Родительский счёт", "Уровень счёта", "Тип строки",
        "Строка родительского счёта", "Источник",
    ]
    child = dict(zip(headers, [cell.value for cell in result_sheet[3]]))
    assert child["Родительский счёт"] == "01"
    assert child["Уровень счёта"] == "2"
    assert child["Тип строки"] == "Детальный счёт"
    assert child["Строка родительского счёта"] == "4"
    assert result_sheet.max_row == 4
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


def test_code_only_tree_preserves_off_balance_codes_and_merged_money_column(tmp_path):
    path = tmp_path / "code-only-tree.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "TDSheet"
    sheet["A6"] = "Счет"
    sheet["B6"] = "Сальдо на начало"
    sheet["E6"] = "Обороты"
    sheet["G6"] = "Сальдо на конец"
    sheet["B7"] = "Дебет"
    sheet["D7"] = "Кредит"
    sheet["E7"] = "Дебет"
    sheet["F7"] = "Кредит"
    sheet["G7"] = "Дебет"
    sheet["H7"] = "Кредит"
    for area in ("A6:A7", "B6:D6", "E6:F6", "G6:H6", "B7:C7"):
        sheet.merge_cells(area)
    rows = [
        (1, "00", [100, 0, 20, 10, 110, 0], 0),
        ("01.01", None, [100, 0, 20, 10, 110, 0], 1),
        (2, "00", [0, 50, 10, 20, 0, 60], 0),
        ("02.01", None, [0, 50, 10, 20, 0, 60], 1),
        (1, "000", [5, 0, 0, 0, 5, 0], 0),
        (2, "000", [0, 3, 0, 0, 0, 3], 0),
        (7, "000", [2, 0, 0, 2, 0, 0], 0),
        ("МЦ", None, [4, 0, 0, 0, 4, 0], 0),
    ]
    for row_number, (account, number_format, amounts, level) in enumerate(rows, 8):
        sheet.cell(row_number, 1, account)
        if number_format:
            sheet.cell(row_number, 1).number_format = number_format
        sheet.cell(row_number, 1).alignment = Alignment(indent=level)
        if level:
            sheet.row_dimensions[row_number].outlineLevel = level
        sheet.merge_cells(start_row=row_number, start_column=2, end_row=row_number, end_column=3)
        for column, amount in zip((2, 4, 5, 6, 7, 8), amounts):
            sheet.cell(row_number, column, amount)
    total_row = 16
    sheet.cell(total_row, 1, "Итого")
    for column, amount in zip((2, 4, 5, 6, 7, 8), (100, 50, 30, 30, 110, 60)):
        sheet.cell(total_row, column, amount)
    book.save(path)
    book.close()

    spec = rule_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert spec.row_rules.account_tree
    assert not spec.row_rules.classify_account_rows
    assert spec.column_mapping["opening_debit"] == 2
    assert spec.column_mapping["account_name"] is None
    parsed = parse_workbook(path, spec, DataType.TRIAL_BALANCE)
    assert [row["account"] for row in parsed.rows][-4:] == ["001", "002", "007", "МЦ"]
    validation = validate_result(parsed, spec)
    assert validation["aggregation"] == "two_digit_root_accounts"
    assert all(item["passed"] for item in validation["control_comparisons"].values())
    assert baseline_is_reliable(spec, validation)

    output = tmp_path / "code-only-result.xlsx"
    export_result(output, parsed.rows, spec, validation)
    exported = load_workbook(output)
    account_cells = list(exported["Результат"]["A"])[1:]
    off_balance = next(cell for cell in account_cells if cell.value == "001")
    assert off_balance.data_type == "s"
    assert off_balance.number_format == "@"
    exported.close()


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
