"""Explicit acceptance bypasses arithmetic checks only."""
from backend.app.core.errors import AppError
from backend.app.db.database import connection, json_load
from backend.app.services.hypothesis_planner import SourceStructure

def arithmetic_failures(v):
    return bool(v and (v.get("row_balance", {}).get("failed")
        or any(not x.get("passed") for x in v.get("control_comparisons", {}).values())))

def latest_diagnostics(transformation_id):
    with connection() as db:
        run = db.execute(
            "SELECT id FROM transformation_runs WHERE transformation_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (transformation_id,)).fetchone()
        events = db.execute(
            "SELECT event_type, details_json FROM transformation_run_events WHERE run_id=? ORDER BY id",
            (run["id"],)).fetchall() if run else []
    spec, validation = None, None
    for event in events:
        d = json_load(event["details_json"], {})
        if event["event_type"] in ("BASELINE_SPEC_CREATED", "FINAL_SPEC_APPLIED", "ERRORS_ACCEPTED"):
            spec, validation = d.get("spec"), None
        if event["event_type"] in ("BASELINE_CHECKED", "LLM_STAGED_STRUCTURE_CHECKED", "CANDIDATE_VALIDATED"):
            spec = d.get("spec", spec)
            validation = d.get("validation", validation)
        if event["event_type"] == "VALIDATION_COMPLETED":
            validation = d
    return spec, validation

def assess_for_acceptance(path, spec, data_type):
    source = SourceStructure(path)
    try:
        plan = source.evaluate(spec, data_type, region_start=1)
    finally:
        source.close()
    e, v = plan.evidence, plan.validation
    safe = (bool(plan.parsed.rows) and e["identity_ratio"] == 1
        and e["numeric_coverage"] == 1 and e["required_coverage"] == 1
        and not e["constant_zero_identity"] and not e["missing_formula_cache"]
        and not e["row_coverage"]["omitted_rows"] and not v["numeric_errors"]
        and not v["missing_required"] and not v["unprocessed_row_numbers"]
        and not v["ambiguous_account_rows"])
    if not safe or not arithmetic_failures(v):
        raise AppError(409, "Принятие доступно только для разобранных данных с арифметическими расхождениями",
                       "CANNOT_ACCEPT_ERRORS")
    return plan
