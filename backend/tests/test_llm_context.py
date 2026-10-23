from types import SimpleNamespace
import json

import pytest
from openpyxl import Workbook

from backend.app.services.hypothesis_planner import SourceStructure
from backend.app.services.llm_context import context_rows, source_context, llm_candidate_summary
from backend.app.services.planner import compact_profile
from backend.app.services.structure_recovery import recovery_profile
from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.models.domain import DataType
from backend.app.services.llm.base import LLMCallResult
from backend.app.services.planner import LLMPlanner
from backend.tests.test_planner_trace import baseline_spec


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "late_header.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet["A1"] = "DO_NOT_SEND_BEGINNING"
    sheet["A37"] = "Перед шапкой"
    sheet["A40"] = "Счет"
    sheet["B40"] = "Обороты"
    sheet.merge_cells("B40:C40")
    sheet["B41"] = "Дебет"
    sheet["C41"] = "Кредит"
    sheet["A42"] = "01"
    sheet["B42"] = "=1+2"
    sheet["A46"] = "Последняя строка окна"
    sheet["A47"] = "DO_NOT_SEND_AFTER"
    sheet["A100"] = "DO_NOT_SEND_TAIL"
    sheet.merge_cells("B99:C100")
    book.save(path)
    book.close()
    spec = baseline_spec().model_copy(update={"header_row": 41, "header_start_row": 40,
                                             "header_end_row": 41, "data_start_row": 42, "data_end_row": 100})
    return path, spec


@pytest.mark.parametrize("end,headers,expected", [
    (100, [(40, 41)], list(range(37, 47))),
    (7, [(1, 2)], list(range(1, 8))),
    (10, [(8, 9)], list(range(5, 11))),
    (100, [(40, 41), (40, 41), (41, 42)], list(range(37, 48))),
    (100, [], list(range(1, 21))),
    (0, [], []),
])
def test_exact_physical_row_windows(end, headers, expected):
    assert context_rows(end, headers) == expected


def assert_window(sheet):
    assert [r["row"] for r in sheet["samples"]] == list(range(37, 47))
    assert sheet["header_ranges"] == [[40, 41]]
    assert sheet["selection"] == "header_plus_3_before_5_after"
    assert sheet["max_row"] == 100  # Full-file bounds are retained, not cut to the sample.
    assert sheet["merged_ranges"] == ["B40:C40"]
    rows = {r["row"]: r for r in sheet["samples"]}
    assert rows[38]["cells"] == {}  # Empty physical rows count among the three/five.
    assert rows[42]["formula_columns"] == [2]
    assert "B42" not in rows[42]["cells"]  # No cached value: do not send the formula as data.
    assert "DO_NOT_SEND" not in str(sheet)


def test_context_reads_exact_late_header_and_refinement_uses_it(source):
    path, spec = source
    assert_window(source_context(path, [spec])["sheets"][0])
    # The old general sample may omit the header completely; read the exact source rows.
    assert_window(compact_profile({"sheets": []}, spec, path)["sheets"][0])


def test_recovery_uses_best_header_not_tail_or_patterns(source):
    path, spec = source
    workbook = SourceStructure(path)
    try:
        selection = SimpleNamespace(candidates=[SimpleNamespace(spec=spec)])
        assert_window(recovery_profile(workbook, selection)["sheets"][0])
        fallback = recovery_profile(workbook, SimpleNamespace(candidates=[]))["sheets"][0]
        assert fallback["selection"] == "no_header_first_20_rows"
        assert [r["row"] for r in fallback["samples"]] == list(range(1, 21))
    finally:
        workbook.close()


def test_candidates_keep_statistics_without_out_of_window_raw_samples():
    original = {"evidence": {"columns": {"account": {"sample_values": ["TAIL"], "nonempty": 100}}}}
    candidate = SimpleNamespace(summary=lambda _: original)
    result = llm_candidate_summary(candidate, "0")
    assert result["evidence"]["columns"]["account"] == {"nonempty": 100}
    assert original["evidence"]["columns"]["account"]["sample_values"] == ["TAIL"]


@pytest.mark.parametrize("mode", ["choice", "refine", "recovery"])
def test_actual_request_contains_only_header_window(source, monkeypatch, mode):
    path, spec = source
    config = LLMConfiguration(active_profile="test", profiles={
        "test": LLMProfile(protocol="openai", base_url="http://unused.test/v1", model="test")})
    planner = LLMPlanner(config)
    candidate = SimpleNamespace(spec=spec, eligible=True, summary=lambda candidate_id: {
        "candidate_id": candidate_id, "evidence": {"columns": {"account": {"sample_values": ["DO_NOT_SEND_TAIL"]}}}})
    selection = SimpleNamespace(candidates=[candidate])
    requests = []

    def complete(adapter, messages, schema, schema_name, trace=None):
        payload = json.loads(messages[1]["content"])
        requests.append(payload)
        assert_window(payload["profile"]["sheets"][0])
        assert "DO_NOT_SEND" not in messages[1]["content"]
        if mode == "recovery":
            return LLMCallResult(json.dumps({"layout": None, "explanation": "Тест", "clarification": "Уточните"}), {}, 1)
        response = {"candidate_id": 0, "reason": "Тест"} if mode == "choice" else {"notes": ["Тест"]}
        return LLMCallResult(json.dumps(response), {}, 1)

    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    if mode == "choice":
        assert planner.choose_candidate(selection, source_path=path) is candidate
    elif mode == "refine":
        profile = {"sheets": [{"name": "ОСВ", "max_row": 100, "max_column": 3}]}
        with pytest.raises(AppError, match="не сформировала допустимых изменений"):
            planner.refine(profile, spec, DataType.TRIAL_BALANCE, feedback="Уточни", source_path=path)
    else:
        with pytest.raises(AppError, match="Уточните"):
            planner.recognize_structure(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", selection)
    assert len(requests) == 1
