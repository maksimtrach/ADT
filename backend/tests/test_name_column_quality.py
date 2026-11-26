import httpx
import pytest
from openpyxl import Workbook

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMProfile
from backend.app.models.domain import DataType
from backend.app.services.hypothesis_planner import SourceStructure, choose_plan
from backend.app.services.rule_planner import rule_candidates
from backend.app.services.llm.openai_compatible import OpenAICompatibleAdapter
from backend.app.services.pipeline import _failure_details


def make_book(path, *, merged=True, alternative=None):
    book = Workbook()
    sheet = book.active
    sheet.append(["Счет", "Наименование счета", "Наименование счета",
                  "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт",
                  "Сальдо на конец Дт", "Сальдо на конец Кт"])
    if merged:
        sheet.merge_cells("B1:C1")
    sheet.append(["51", "Расчетные счета", alternative, 100, None, 50, 20, 130, None])
    sheet.append(["80", "Уставный капитал", alternative, None, 100, 20, 50, None, 130])
    book.save(path)
    book.close()


def test_empty_merged_name_shadow_is_not_a_candidate(tmp_path):
    path = tmp_path / "merged.xlsx"
    make_book(path)
    specs = rule_candidates(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert all(spec.column_mapping.get("account_name") != 3 for spec in specs)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    assert selection.best.spec.column_mapping["account_name"] == 2


@pytest.mark.parametrize("alternative", [None, 123, "Другое название"])
def test_assigned_name_is_scored_without_replacement(tmp_path, alternative):
    path = tmp_path / "names.xlsx"
    make_book(path, merged=False, alternative=alternative)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    source = SourceStructure(path)
    try:
        good = selection.best.spec.model_copy(deep=True)
        good.column_mapping["account_name"] = 2
        other = good.model_copy(deep=True)
        other.column_mapping["account_name"] = 3
        first = source.evaluate(good, DataType.TRIAL_BALANCE)
        second = source.evaluate(other, DataType.TRIAL_BALANCE)
        assert second.spec.column_mapping["account_name"] == 3
        if alternative == "Другое название":
            assert second.score == first.score
            assert not selection.confident
        else:
            assert first.score - second.score == pytest.approx(10)
            assert second.evidence["account_name_quality"]["score"] == 0
        absent = good.model_copy(deep=True)
        absent.column_mapping["account_name"] = None
        assert source.evaluate(absent, DataType.TRIAL_BALANCE).eligible
    finally:
        source.close()


def test_merged_header_with_real_values_retains_alternative(tmp_path):
    path = tmp_path / "real.xlsx"
    make_book(path, alternative="Другое название")
    specs = rule_candidates(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert any(spec.column_mapping.get("account_name") == 3 for spec in specs)


def test_reasoning_only_response_is_reported_as_llm_error(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "", "reasoning_content": '{"candidate_id": 0}'}}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    adapter = OpenAICompatibleAdapter("test", LLMProfile(
        protocol="openai", base_url="http://unused.test/v1", model="test"))
    events = []
    with pytest.raises(AppError) as error:
        adapter.complete([], {}, "Test", lambda kind, detail: events.append(kind))
    assert error.value.code == "INVALID_LLM_RESPONSE"
    details = _failure_details(error.value, "PLANNING")
    assert "content" in details["reason"]
    assert "thinking" in details["recommendation"]
    assert events == ["LLM_REQUEST", "LLM_RESPONSE", "LLM_FAILED"]
