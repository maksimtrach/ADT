import json
from pathlib import Path
from time import perf_counter

import pytest
from openpyxl import Workbook

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.models.domain import DataType
from backend.app.services.feedback_rules import explicit_column_feedback
from backend.app.services.file_profiler import profile_workbook
from backend.app.services.hypothesis_planner import choose_plan
from backend.app.services.llm.base import LLMCallResult
from backend.app.services.planner import LLMPlanner


def ambiguous_workbook(path, formulas=False):
    book = Workbook()
    sheet = book.active
    sheet.append(["№ счета по МСФО", None, "№ счета по РСБУ", "Наименование счета по РСБУ",
                  "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт",
                  "Сальдо на конец Дт", "Сальдо на конец Кт"])
    sheet.append(["=C2" if formulas else "101", None, "01", "Основные средства", 100, None, 50, 20, 130, None])
    sheet.append(["=C3" if formulas else "101.01", None, "01.01", "Объекты", 100, None, 50, 20, 130, None])
    book.save(path)
    book.close()


def test_missing_formula_cache_loses_to_real_account_column(tmp_path):
    path = tmp_path / "formulas.xlsx"
    ambiguous_workbook(path, formulas=True)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    assert selection.best.spec.column_mapping["account"] == 3
    assert any(item.evidence["missing_formula_cache"] > 0 for item in selection.candidates)
    profile = profile_workbook(path)
    sample = next(row for row in profile["sheets"][0]["sample_rows"] if row["row"] == 2)
    assert sample["values"][0] is None
    assert sample["formula_columns"] == [1]


def test_two_plausible_account_columns_remain_ambiguous(tmp_path):
    path = tmp_path / "ambiguous.xlsx"
    ambiguous_workbook(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert not selection.confident
    assert {item.spec.column_mapping["account"] for item in selection.candidates if item.eligible} == {1, 3}


@pytest.mark.parametrize("answer", [None, 999])
def test_llm_abstention_or_invalid_choice_does_not_trigger_second_model(tmp_path, monkeypatch, answer):
    path = tmp_path / "ambiguous.xlsx"
    ambiguous_workbook(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    config = LLMConfiguration(active_profile="first", fallback_profiles=["second"], profiles={
        name: LLMProfile(protocol="openai", base_url="http://unused.test/v1", model=name)
        for name in ("first", "second")
    })
    calls = []

    def complete(adapter, messages, schema, schema_name, trace=None):
        calls.append(adapter.profile_name)
        assert schema_name == "CandidateChoice"
        request = json.loads(messages[1]["content"])
        assert request["candidates"]
        assert "baseline_spec" not in request
        return LLMCallResult(json.dumps({"candidate_id": answer, "reason": "Нет оснований"}), {}, 1)

    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    with pytest.raises(AppError):
        LLMPlanner(config).choose_candidate(selection)
    assert calls == ["first"]


def test_explicit_column_instruction_is_not_a_value_override():
    assert explicit_column_feedback("Загрузи номера счетов из колонки C") == {"account": 3}
    assert explicit_column_feedback("Брать счета из колонки С") == {"account": 3}
    assert explicit_column_feedback("строки 5–8 относятся к счету 01") is None
    assert explicit_column_feedback("брать счета из колонки C и суммы из E") is None


def test_llm_can_only_select_an_evaluated_candidate(tmp_path, monkeypatch):
    path = tmp_path / "ambiguous.xlsx"
    ambiguous_workbook(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    chosen_id = next(i for i, item in enumerate(selection.candidates)
                     if item.eligible and item.spec.column_mapping["account"] == 3)
    config = LLMConfiguration(active_profile="test", profiles={
        "test": LLMProfile(protocol="openai", base_url="http://unused.test/v1", model="test"),
    })

    def complete(*args, **kwargs):
        return LLMCallResult(json.dumps({"candidate_id": chosen_id, "reason": "По шапке"}), {}, 1)

    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    assert LLMPlanner(config).choose_candidate(selection) is selection.candidates[chosen_id]


def test_short_user_workbook_selects_ras_without_model():
    path = Path("test-data/complex/ОСВ _тест2_кратк.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    started = perf_counter()
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    result = selection.best
    assert selection.confident
    assert result.spec.column_mapping["account"] == 3
    assert result.spec.column_mapping["account_name"] == 4
    assert len(result.parsed.rows) == 132
    assert result.parsed.rows[0]["account"] == "01"
    assert result.parsed.rows[1]["account"] == "01.01"
    assert result.validation["control_row_count"] == 35
    assert result.validation["status"] == "WARNINGS"
    assert result.validation["row_balance"] == {"checked": 132, "failed": 0, "examples": []}
    assert result.validation["duplicate_accounts"] == ["19.04", "19.08"]
    assert result.validation["independent_totals_available"] is False
    assert result.validation["calculated_sums"]["turnover_debit"] == 3575952029.83
    print(f"Short workbook hypotheses + parsing: {perf_counter() - started:.3f}s")
