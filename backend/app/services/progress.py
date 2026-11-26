"""Progress derived from the current run, including historical attempts."""
from datetime import datetime, timezone
from backend.app.db.database import json_load

STAGES = {
    "QUEUED": "ожидание запуска", "PROFILING": "чтение и анализ Excel",
    "PLANNING": "определение структуры таблицы", "TRANSFORMING": "преобразование данных",
    "VALIDATING": "проверка строк и итоговых сумм", "EXPORTING": "формирование Excel",
}
OPERATIONS = {
    "FILE_OPENED": "чтение и анализ Excel",
    "DOCUMENT_PROFILE_SELECTED": "определение шапки и колонок",
    "HYPOTHESES_GENERATION_STARTED": "определение шапки и колонок · формирование гипотез",
    "HYPOTHESES_PREPARATION_STARTED": "определение шапки и колонок · подготовка данных для проверки гипотез",
    "HYPOTHESIS_EVALUATION_STARTED": "определение шапки и колонок · проверка гипотезы",
    "HYPOTHESES_SELECTION_STARTED": "определение шапки и колонок · выбор лучшей гипотезы",
    "HYPOTHESES_EVALUATED": "проверка вариантов структуры",
    "MEMORY_SEARCH": "поиск подхода в памяти",
    "MEMORY_CANDIDATE_CHECKED": "проверка подхода из памяти",
    "BASELINE_SPEC_CREATED": "пробный разбор и проверка сумм",
    "BASELINE_CHECKED": "выбор правил преобразования",
    "LLM_STRUCTURE_RECOVERY_STARTED": "распознавание структуры через LLM",
    "LLM_HEADER_RECOGNITION_STARTED": "распознавание шапки через LLM",
    "LLM_HEADER_ACCEPTED": "определение итоговых строк и границ данных",
    "LLM_HEADER_SKIPPED": "определение итоговых строк и границ данных",
    "LLM_FOOTER_RECOGNITION_STARTED": "распознавание итоговых строк через LLM",
    "LLM_FOOTER_ACCEPTED": "проверка колонок и границ данных",
    "LLM_FOOTER_SKIPPED": "проверка колонок и границ данных",
    "LLM_STAGED_STRUCTURE_CHECKED": "проверка строк и итоговых сумм",
    "LLM_STRUCTURE_CHECKED": "проверка строк и итоговых сумм",
    "CANDIDATE_VALIDATED": "проверка строк и итоговых сумм",
    "FINAL_SPEC_APPLIED": "преобразование данных",
    "PARSING_COMPLETED": "проверка строк и итоговых сумм",
    "VALIDATION_COMPLETED": "проверка строк и итоговых сумм",
    "RESULT_EXPORTED": "формирование Excel",
}


def progress_details(run, row, events):
    if not run:
        return {}
    terminal = row["status"] in ("COMPLETED", "FAILED", "CANCELLED")
    finished = (run["finished_at"] or row["updated_at"]) if terminal else None
    start = datetime.fromisoformat(run["started_at"])
    end = datetime.fromisoformat(finished) if finished else datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    stage = row["current_stage"]
    if terminal:
        stage = next((event["stage"] for event in reversed(events)
                      if event["stage"] in STAGES and event["event_type"] not in
                      ("MEMORY_CASE_SAVED", "MEMORY_UNAVAILABLE")), "QUEUED")
    if terminal:
        for event in reversed(events):
            if event["event_type"] in ("PROCESSING_FAILED", "PROCESSING_CANCELLED"):
                stage = json_load(event["details_json"], {}).get("stage") or stage
                break
    operation = STAGES.get(stage, "обработка файла")
    llm_operation = None
    for event in events:
        if event["stage"] != stage:
            continue
        kind = event["event_type"]
        if kind in OPERATIONS:
            operation = OPERATIONS[kind]
            if kind == "HYPOTHESIS_EVALUATION_STARTED":
                details = json_load(event["details_json"], {})
                number, total = details.get("hypothesis_number"), details.get("total")
                if (type(number) is int and type(total) is int and 1 <= number <= total):
                    operation += f" {number} из {total}"
            llm_operation = operation if kind.endswith("_STARTED") and kind.startswith("LLM_") else None
        elif kind in ("LLM_REQUEST", "LLM_PROFILE_FALLBACK"):
            operation = llm_operation or "запрос к LLM"
        elif kind == "LLM_RESPONSE":
            operation = "проверка ответа LLM"
    return {
        "processing_run_id": run["id"], "run_started_at": run["started_at"],
        "run_finished_at": finished, "elapsed_seconds": max(0, (end - start).total_seconds()),
        "current_operation": operation,
    }
