import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.models.domain import DataType
from backend.app.services.account_structure import MONEY_KEYS
from backend.app.services.hypothesis_planner import choose_plan, PlanSelection, SourceStructure
from backend.app.services.llm.base import LLMCallResult
from backend.app.services.planner import LLMPlanner
from backend.app.services.rule_planner import standard_money_mapping
from backend.app.services.structure_recovery import build_recovered_spec, recovery_profile, StructureAnswer, StructureLayout


def recovery_workbook(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Счет", "Наименование", "Начало +", "Начало -", "Приход", "Расход", "Конец +", "Конец -"])
    sheet.append(["52", "Валютные счета", 100, 0, 20, 10, 110, 0])
    sheet.append(["USD", None, 100, 0, 20, 10, 110, 0])
    sheet.append([None, None, 10, 0, 2, 1, 11, 0])
    book.save(path)
    book.close()


def answer_layout():
    return {"layout": {"sheet_name": "ОСВ", "header_start_row": 1, "header_end_row": 1,
                       "data_start_row": 2, "data_end_row": 4, "row_mode": "account_tree",
                       "column_mapping": {"account": 1, "account_name": 2, **dict(zip(MONEY_KEYS, range(3, 9)))}},
            "explanation": "Шесть денежных колонок, счёт и валютные расшифровки", "clarification": None}


def fake_planner(monkeypatch, answer):
    config = LLMConfiguration(active_profile="first", fallback_profiles=["second"], profiles={
        name: LLMProfile(protocol="openai", base_url="http://unused.test/v1", model=name) for name in ("first", "second")
    })
    calls = []

    def complete(adapter, messages, schema, schema_name, trace=None):
        calls.append(adapter.profile_name)
        assert schema_name == "StructureAnswer"
        payload = json.loads(messages[1]["content"])
        assert payload["profile"]["sheets"][0]["samples"]
        return LLMCallResult(json.dumps(answer, ensure_ascii=False), {}, 1)

    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    return LLMPlanner(config), calls


def test_standard_order_requires_three_pairs_and_no_explicit_sides():
    headers = ["счет", "наименование", "сальдо на начало", "сальдо на начало", "обороты", "обороты", "сальдо на конец", "сальдо на конец"]
    assert standard_money_mapping(headers) == dict(zip(MONEY_KEYS, range(3, 9)))
    assert standard_money_mapping([*headers[:3], "дебет", *headers[4:]]) is None
    assert standard_money_mapping([*headers[:4], "", *headers[4:]]) is None


def test_llm_recovers_layout_and_never_sums_currency_rows(tmp_path, monkeypatch):
    path = tmp_path / "recovery.xlsx"
    recovery_workbook(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert not any(c.eligible for c in selection.candidates)
    planner, calls = fake_planner(monkeypatch, answer_layout())
    events = []
    result = planner.recognize_structure(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", selection,
                                         trace=lambda event, details: events.append(event))
    assert calls == ["first"]
    assert result.eligible
    assert len(result.parsed.rows) == 3  # Last unnamed currency amount must not become a total.
    assert result.parsed.rows[-1]["account"] == "52"
    assert result.parsed.rows[-1]["currency"] == "USD"
    assert result.parsed.rows[1]["source_code"] == "USD"
    assert result.parsed.rows[1]["_source_refs"]["source_code"].endswith("A3")
    assert result.validation["calculated_sums"]["opening_debit"] == 100
    assert result.validation["row_classification"] == {"account": 1, "currency": 1, "unlabelled": 1}
    assert not result.parsed.declared_totals
    assert events[-1] == "LLM_STRUCTURE_ACCEPTED"


@pytest.mark.parametrize("invalid", ["duplicate_column", "truncate", "skip_data", "change_values", "invalid_column", "zero_column", "negative_column", "string_column", "fractional_column", "boolean_column", "missing_money", "abstain"])
def test_bad_llm_layout_rejected_without_trying_another_model(tmp_path, monkeypatch, invalid):
    path = tmp_path / "recovery.xlsx"
    recovery_workbook(path)
    answer = answer_layout()
    layout = answer["layout"]
    if invalid == "duplicate_column": layout["column_mapping"]["turnover_debit"] = 3
    if invalid == "truncate": layout["data_end_row"] = 3
    if invalid == "skip_data": layout.update(header_end_row=2, data_start_row=3)
    if invalid == "change_values": layout["value_overrides"] = [{"value": 0}]
    if invalid == "invalid_column": layout["column_mapping"]["opening_debit"] = 999
    if invalid == "zero_column": layout["column_mapping"]["account"] = 0
    if invalid == "negative_column": layout["column_mapping"]["account"] = -1
    if invalid == "string_column": layout["column_mapping"]["account"] = "1"
    if invalid == "fractional_column": layout["column_mapping"]["account"] = 1.5
    if invalid == "boolean_column": layout["column_mapping"]["account"] = True
    if invalid == "missing_money": layout["column_mapping"].pop("opening_debit")
    if invalid == "abstain": answer.update(layout=None, clarification="Уточните единицы сумм")
    planner, calls = fake_planner(monkeypatch, answer)
    with pytest.raises(AppError):
        planner.recognize_structure(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", PlanSelection([], False, 0))
    assert calls == ["first"]


def test_structure_schema_requires_positive_column_numbers_and_allows_null():
    schema = StructureAnswer.model_json_schema()
    column = schema["$defs"]["StructureLayout"]["properties"]["column_mapping"]["additionalProperties"]
    assert {"type": "integer", "minimum": 1} in column["anyOf"]
    assert {"type": "null"} in column["anyOf"]
    answer = answer_layout()
    answer["layout"]["column_mapping"]["subaccount"] = None
    assert StructureAnswer.model_validate(answer).layout.column_mapping["account"] == 1


def test_llm_account_tree_allows_missing_account_name(tmp_path):
    path = tmp_path / "code-tree-recovery.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Счет", "Начало Дт", "Начало Кт", "Оборот Дт", "Оборот Кт", "Конец Дт", "Конец Кт"])
    sheet.append(["01", 100, 0, 20, 10, 110, 0])
    sheet.append(["01.01", 100, 0, 20, 10, 110, 0])
    book.save(path)
    book.close()
    source = SourceStructure(path)
    try:
        layout = StructureLayout(
            sheet_name="ОСВ", header_start_row=1, header_end_row=1, data_start_row=2, data_end_row=3,
            row_mode="account_tree", column_mapping={"account": 1, **dict(zip(MONEY_KEYS, range(2, 8)))},
        )
        spec = build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    finally:
        source.close()
    assert spec.row_rules.account_tree
    assert not spec.row_rules.classify_account_rows
    assert spec.column_mapping["account_name"] is None


def test_bad_source_arithmetic_is_not_overridden_by_model(tmp_path, monkeypatch):
    from openpyxl import load_workbook
    path = tmp_path / "recovery.xlsx"
    recovery_workbook(path)
    book = load_workbook(path)
    book.active["G2"] = 999
    book.save(path)
    book.close()
    planner, calls = fake_planner(monkeypatch, answer_layout())
    with pytest.raises(AppError, match="не прошла проверки"):
        planner.recognize_structure(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", PlanSelection([], False, 0))
    assert calls == ["first"]


def test_real_2017_workbook_preserves_every_row_and_ambiguous_codes():
    path = Path("test-data/complex/ОСВ_2017_тест.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    result = selection.best
    assert result.spec.row_rules.standard_money_order
    assert len(result.parsed.rows) == 233
    assert result.validation["row_classification"] == {"account": 163, "currency": 32, "unlabelled": 38}
    assert result.validation["row_balance"] == {"checked": 230, "failed": 0, "examples": []}
    assert not result.validation["aggregation_available"]
    assert result.validation["calculated_sums"] == {}
    rows = {row["_source_row"]: row for row in result.parsed.rows}
    assert rows[5]["account"] == rows[231]["account"] == "1"
    assert rows[236]["account"] == "ЗАБ"
    assert rows[237]["account"] == "КВ"
    assert rows[54]["account"] == rows[55]["account"] == "52"
    assert rows[55]["parent_source_row"] == "53"
    assert rows[55]["turnover_debit"] == 132.98
    source = SourceStructure(path)
    try:
        for row in result.parsed.rows:
            for key, column in zip(MONEY_KEYS, range(4, 10)):
                assert row[key] == source.values.active.cell(row["_source_row"], column).value
        profile = recovery_profile(source, selection)
        samples = {r["row"] for r in profile["sheets"][0]["samples"]}
        spec = selection.best.spec
        assert samples == set(range(max(1, (spec.header_start_row or spec.header_row) - 3),
                                    min(237, (spec.header_end_row or spec.header_row) + 5) + 1))
    finally:
        source.close()
