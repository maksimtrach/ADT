import json
from types import SimpleNamespace

import pytest

from backend.app.models.domain import DataType
from backend.app.services import hypothesis_planner as planner
from backend.app.services.progress import progress_details


def test_hypothesis_progress_precedes_evaluation_and_keeps_number_after_sort(monkeypatch):
    events = []
    monkeypatch.setattr(planner, "CheckedResult", lambda *args: None)
    specs = [
        SimpleNamespace(sheet_name="Sheet", data_start_row=2, data_end_row=3,
                        header_start_row=1, header_end_row=1, control_rows=[],
                        column_mapping={"account": column}, hierarchy=None)
        for column in (1, 2)
    ]
    monkeypatch.setattr(planner, "rule_candidates", lambda *args: specs)

    class Source:
        closed = False

        def __init__(self, path):
            assert events[-1] == ("HYPOTHESES_PREPARATION_STARTED", {"total": 2})

        def evaluate(self, spec, data_type, *, region_start):
            number = specs.index(spec) + 1
            assert events[-1] == ("HYPOTHESIS_EVALUATION_STARTED", {
                "hypothesis_number": number, "total": 2, "sheet": "Sheet",
            })
            return planner.EvaluatedPlan(spec, None, {}, number * 10, {}, True)

        def close(self):
            Source.closed = True

    monkeypatch.setattr(planner, "SourceStructure", Source)
    result = planner.choose_plan("unused", DataType.TRIAL_BALANCE, "trial_balance_flat_v1",
                                 trace=lambda kind, details: events.append((kind, details)))
    assert Source.closed
    assert [kind for kind, _ in events] == [
        "HYPOTHESES_GENERATION_STARTED", "HYPOTHESES_PREPARATION_STARTED",
        "HYPOTHESIS_EVALUATION_STARTED", "HYPOTHESIS_EVALUATION_STARTED",
        "HYPOTHESES_SELECTION_STARTED",
    ]
    assert [item["hypothesis_number"] for item in result.summary()["candidates"]] == [2, 1]
    # Positional IDs used by the LLM keep their original contract.
    assert [item["candidate_id"] for item in result.summary()["candidates"]] == ["0", "1"]


@pytest.mark.parametrize(("kind", "details", "expected"), [
    ("HYPOTHESES_GENERATION_STARTED", {}, "формирование гипотез"),
    ("HYPOTHESES_PREPARATION_STARTED", {}, "подготовка данных для проверки гипотез"),
    ("HYPOTHESIS_EVALUATION_STARTED", {"hypothesis_number": 3, "total": 7}, "проверка гипотезы 3 из 7"),
    ("HYPOTHESIS_EVALUATION_STARTED", {}, "проверка гипотезы"),
    ("HYPOTHESIS_EVALUATION_STARTED", {"hypothesis_number": 8, "total": 7}, "проверка гипотезы"),
    ("HYPOTHESES_SELECTION_STARTED", {}, "выбор лучшей гипотезы"),
])
def test_hypothesis_operation(kind, details, expected):
    run = {"id": "run", "started_at": "2026-11-24T10:00:00+00:00", "finished_at": None}
    row = {"status": "RUNNING", "current_stage": "PLANNING", "updated_at": run["started_at"]}
    events = [{"stage": "PLANNING", "event_type": kind, "details_json": json.dumps(details)}]
    result = progress_details(run, row, events)
    assert result["current_operation"] == "определение шапки и колонок · " + expected
    # A failure preserves the last hypothesis number and freezes elapsed time.
    row.update(status="FAILED", current_stage="FAILED", updated_at="2026-11-24T10:00:38+00:00")
    events.append({"stage": "FAILED", "event_type": "PROCESSING_FAILED",
                   "details_json": json.dumps({"stage": "PLANNING"})})
    failed = progress_details(run, row, events)
    assert failed["current_operation"] == result["current_operation"]
    assert failed["elapsed_seconds"] == 38
