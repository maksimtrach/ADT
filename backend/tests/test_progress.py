import json
from backend.app.services.progress import progress_details


def test_failed_progress_keeps_operation_and_finish_time():
    run = {"id": "attempt-2", "started_at": "2026-11-22T10:00:00+00:00",
           "finished_at": "2026-11-22T10:00:38+00:00"}
    row = {"status": "FAILED", "current_stage": "FAILED", "updated_at": run["finished_at"]}
    events = [
        {"stage": "PLANNING", "event_type": "LLM_HEADER_RECOGNITION_STARTED"},
        {"stage": "PLANNING", "event_type": "LLM_REQUEST"},
        {"stage": "FAILED", "event_type": "PROCESSING_FAILED", "details_json": json.dumps({"stage": "PLANNING"})},
        {"stage": "PLANNING", "event_type": "MEMORY_CASE_SAVED"},
    ]
    result = progress_details(run, row, events)
    assert result["elapsed_seconds"] == 38
    assert result["processing_run_id"] == "attempt-2"
    assert result["current_operation"] == "распознавание шапки через LLM"


def test_failure_before_first_stage_event_uses_recorded_stage():
    run = {"id": "attempt", "started_at": "2026-11-22T10:00:00+00:00", "finished_at": None}
    row = {"status": "FAILED", "current_stage": "FAILED", "updated_at": "2026-11-22T10:00:02+00:00"}
    events = [
        {"stage": "QUEUED", "event_type": "FILE_OPENED"},
        {"stage": "FAILED", "event_type": "PROCESSING_FAILED", "details_json": json.dumps({"stage": "PROFILING"})},
    ]
    result = progress_details(run, row, events)
    assert result["elapsed_seconds"] == 2
    assert result["current_operation"] == "чтение и анализ Excel"
