import json
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.services.candidate_choice import candidate_request
from backend.app.services.llm.base import LLMCallResult
from backend.app.services.planner import LLMPlanner
from backend.tests.test_planner_trace import baseline_spec


def candidate(name_column, *, eligible=True, end=20):
    spec = baseline_spec().model_copy(deep=True)
    spec.column_mapping = {"account": 1, "account_name": name_column, "turnover_debit": 4}
    spec.data_end_row = end
    return SimpleNamespace(spec=spec, eligible=eligible, summary=lambda identifier: {
        "candidate_id": identifier, "hypothesis_number": 99, "score": 94.9,
        "evidence": {"rows": 18, "identity_ratio": 1, "row_balance": {"checked": 18, "failed": 0},
                     "columns": {"account_name": {"header": "Наименование", "nonempty": 18 if name_column == 2 else 0,
                                                   "sample_values": ["NEVER_SEND_OUT_OF_WINDOW"]}}},
    })


def selection():
    return SimpleNamespace(candidates=[candidate(2), candidate(3, eligible=False), candidate(3)])


def planner():
    return LLMPlanner(LLMConfiguration(active_profile="test", profiles={
        "test": LLMProfile(protocol="openai", base_url="http://unused.test/v1", model="test", reasoning_effort="none"),
    }))


def test_choice_has_shared_context_and_only_differences():
    request = candidate_request(selection())
    assert request["common"]["mapping"] == {"account": 1, "turnover_debit": 4}
    assert [item["candidate_id"] for item in request["candidates"]] == [0, 2]
    assert request["candidates"][0]["mapping"] == {"account_name": 2}
    assert request["candidates"][1]["mapping"] == {"account_name": 3}
    assert request["common"]["checks"]["rows"] == 18
    assert request["candidates"][0]["checks"] == {}
    text = json.dumps(request, ensure_ascii=False)
    assert "hypothesis_number" not in text
    assert "NEVER_SEND_OUT_OF_WINDOW" not in text
    assert "МСФО" not in text
    assert "score" not in text
    assert "account_name" in request["questions"][0]


@pytest.mark.parametrize("answer,expected_code", [
    ({"candidate_id": 2, "reason": "По данным"}, None),
    ({"candidate_id": None, "reason": "Недостаточно данных"}, "STRUCTURE_AMBIGUOUS"),
    ({"candidate_id": 1, "reason": "Недопустимый вариант"}, "STRUCTURE_AMBIGUOUS"),
    ({"reason": "Нет идентификатора"}, "INVALID_LLM_SPEC"),
    ({"candidate_id": "0", "reason": "Строка вместо числа"}, "INVALID_LLM_SPEC"),
    ({"candidate_id": False, "reason": "Логическое значение"}, "INVALID_LLM_SPEC"),
    ({"candidate_id": 0, "reason": "Лишний ключ", "mapping": {}}, "INVALID_LLM_SPEC"),
])
def test_choice_schema_and_runtime_validate_identifiers(monkeypatch, answer, expected_code):
    plans = selection()
    def complete(adapter, messages, schema, schema_name, trace=None):
        assert schema_name == "CandidateChoice"
        assert set(schema["required"]) == {"candidate_id", "reason"}
        assert schema["additionalProperties"] is False
        assert schema["properties"]["candidate_id"]["anyOf"] == [
            {"type": "integer", "enum": [0, 2]}, {"type": "null"}]
        assert [item["candidate_id"] for item in json.loads(messages[1]["content"])["candidates"]] == [0, 2]
        return LLMCallResult(json.dumps(answer), {}, 1)
    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    if expected_code:
        with pytest.raises(AppError) as exc:
            planner().choose_candidate(plans)
        assert exc.value.code == expected_code
    else:
        assert planner().choose_candidate(plans) is plans.candidates[2]


def test_source_window_focus_and_disputed_footer(tmp_path):
    path = tmp_path / "source.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Отчет", None, None, None])
    sheet.append(["Счет", "Наименование", None, "Оборот"])
    sheet.merge_cells("B2:C2")
    for number in range(3, 21):
        sheet.append([str(number), f"Тестовое название {number}", None, 10])
    sheet.append([None, "Итого", None, 180])
    sheet["B40"] = "DO_NOT_SEND_FAR_TAIL"
    book.save(path)
    book.close()
    plans = selection()
    profile = candidate_request(plans, path)["profile"]["sheets"][0]
    assert [row["row"] for row in profile["samples"]] == list(range(1, 8))
    assert "D3" not in str(profile)
    assert "Тестовое название 3" in str(profile)
    assert "DO_NOT_SEND" not in str(profile)
    plans.candidates[2].spec.data_end_row = 21
    plans.candidates[0].spec.control_rows = [21]
    request = candidate_request(plans, path)
    profile = request["profile"]["sheets"][0]
    assert 21 in profile["boundary_rows"]
    assert "Итого" in str(profile)
    assert "D21" in str(profile)
    assert "DO_NOT_SEND" not in str(profile)
    assert "data_rows" not in request["common"]
    assert request["candidates"][0]["data_rows"] == [3, 20]
    assert request["candidates"][1]["data_rows"] == [3, 21]
