from __future__ import annotations

import json

import pytest
from openpyxl import Workbook, load_workbook

from backend.app.models.domain import DataType
from backend.app.services.file_profiler import profile_workbook, validate_xlsx
from backend.app.services.exporter import export_result
from backend.app.services.parser import parse_workbook
from backend.app.services.planner import rule_plan
from backend.app.services.templates import get_template
from backend.app.services.validator import validate_result
from scripts.generate_test_files import EXPECTED, OUTPUT, generate_all


@pytest.fixture(scope="session", autouse=True)
def generated_files():
    generate_all()


@pytest.mark.parametrize(
    ("filename", "data_type", "template_id"),
    [
        ("01-simple-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("02-grouped-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("03-merged-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("04-unusual-journal", DataType.JOURNAL, "journal_flat_v1"),
        ("05-hierarchical-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
    ],
)
def test_synthetic_file_matches_expected(filename: str, data_type: DataType, template_id: str):
    path = OUTPUT / f"{filename}.xlsx"
    validate_xlsx(path, 25 * 1024 * 1024)
    profile = profile_workbook(path)
    assert profile["sheet_count"] == 1
    assert profile["sheets"][0]["nonempty_rows"] >= 3
    spec = rule_plan(path, data_type, template_id)
    parsed = parse_workbook(path, spec, data_type)
    expected = json.loads((EXPECTED / f"{filename}.json").read_text(encoding="utf-8"))
    keys = [column.key for column in spec.target_columns]
    actual = [{key: row.get(key) for key in keys} for row in parsed.rows]
    assert actual == expected
    validation = validate_result(parsed, spec)
    assert validation["status"] in {"PASSED", "WARNINGS"}
    assert not validation["missing_required"]


def test_flat_trial_balance_target_has_account_name_as_third_field(tmp_path):
    template = get_template("trial_balance_flat_v1")
    assert [column.key for column in template.columns[:3]] == ["account", "subaccount", "account_name"]
    assert [column.label for column in template.columns[:3]] == ["Счет", "Субсчет", "Наименование счёта"]

    path = OUTPUT / "01-simple-trial-balance.xlsx"
    spec = rule_plan(path, DataType.TRIAL_BALANCE, template.id)
    assert [column.key for column in spec.target_columns] == [column.key for column in template.columns]
    parsed = parse_workbook(path, spec, DataType.TRIAL_BALANCE)
    validation = validate_result(parsed, spec)
    output = tmp_path / "flat-result.xlsx"
    export_result(output, parsed.rows, spec, validation)
    book = load_workbook(output, read_only=True)
    headers = [cell.value for cell in book["Результат"][1]]
    book.close()
    assert headers[:3] == ["Счет", "Субсчет", "Наименование счёта"]
    assert headers[-5:] == [
        "Родительский счёт", "Уровень счёта", "Тип строки",
        "Строка родительского счёта", "Источник",
    ]
    assert parsed.rows[0]["parent_account"] is None
    assert parsed.rows[0]["account_level"] == "2"
    assert parsed.rows[0]["row_type"] == "Детальный счёт"
    assert parsed.rows[0]["parent_source_row"] is None


def test_multiline_header_defines_dynamic_analytic_hierarchy(tmp_path):
    path = OUTPUT / "05-hierarchical-trial-balance.xlsx"
    spec = rule_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")

    assert (spec.header_start_row, spec.header_end_row) == (2, 3)
    assert spec.data_start_row == 4
    assert spec.hierarchy is not None
    assert [level.key for level in spec.hierarchy.levels] == ["account", "organization", "cost_item"]
    assert [column.key for column in spec.target_columns[:3]] == ["account", "organization", "cost_item"]
    parsed = parse_workbook(path, spec, DataType.TRIAL_BALANCE)
    validation = validate_result(parsed, spec)
    result_path = tmp_path / "dynamic-result.xlsx"
    export_result(result_path, parsed.rows, spec, validation)
    result_book = load_workbook(result_path, read_only=True)
    headers = [cell.value for cell in result_book["Результат"][1]]
    result_book.close()
    assert headers[:3] == ["Счет", "Организация", "Статья затрат"]


@pytest.mark.parametrize(
    ("analytic_label", "expected_key"),
    [
        ("Основное средство", "fixed_asset"),
        ("Нематериальный актив", "intangible_asset"),
        ("Номер кредитного договора", "loan_agreement"),
        ("Пользовательское измерение", "analytics_1"),
    ],
)
def test_dynamic_analytic_type_is_taken_from_header(tmp_path, analytic_label: str, expected_key: str):
    path = tmp_path / "dynamic-analytics.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ОСВ"
    sheet.append([f"Счет\nОрганизация\n{analytic_label}", "Оборот Дт"])
    sheet.append(["20", None])
    sheet.append(["ООО Компания", None])
    sheet.append(["Значение аналитики", 100])
    workbook.save(path)

    spec = rule_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")

    assert spec.hierarchy is not None
    assert [level.key for level in spec.hierarchy.levels] == ["account", "organization", expected_key]
