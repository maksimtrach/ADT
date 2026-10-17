from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.db.database import connection, json_dump, json_load, record_audit, utc_now
from backend.app.models.domain import DataType, ProcessingStage, TransformationSpec
from backend.app.services.exporter import export_result
from backend.app.services.file_profiler import profile_workbook
from backend.app.services.parser import parse_workbook
from backend.app.services.planner import OllamaPlanner, rule_plan
from backend.app.services.validator import validate_result


def _set_stage(transformation_id: str, stage: ProcessingStage) -> None:
    with connection() as db:
        db.execute(
            "UPDATE transformations SET status = 'RUNNING', current_stage = ?, updated_at = ? WHERE id = ?",
            (stage.value, utc_now(), transformation_id),
        )


def process_transformation(transformation_id: str, feedback: str | None = None) -> None:
    with connection() as db:
        transformation = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        if not transformation:
            return
        source_path = Path(transformation["source_path"])
        data_type = DataType(transformation["data_type"])
        template_id = transformation["template_id"]
        user_id = transformation["user_id"]
        previous_version = transformation["current_version"]

    llm_used = False
    extra_warnings: list[str] = []
    try:
        _set_stage(transformation_id, ProcessingStage.PROFILING)
        profile = profile_workbook(source_path)

        _set_stage(transformation_id, ProcessingStage.PLANNING)
        baseline: TransformationSpec
        if previous_version:
            with connection() as db:
                latest = db.execute(
                    "SELECT spec_json FROM transformation_versions WHERE transformation_id = ? AND version = ?",
                    (transformation_id, previous_version),
                ).fetchone()
            baseline = TransformationSpec.model_validate(json_load(latest["spec_json"], {})) if latest else rule_plan(source_path, data_type, template_id)
        else:
            baseline = rule_plan(source_path, data_type, template_id)

        planner = OllamaPlanner()
        spec = baseline
        if feedback:
            spec = planner.refine(profile, baseline, data_type, feedback)
            llm_used = True
        elif planner.available():
            try:
                spec = planner.refine(profile, baseline, data_type)
                llm_used = True
            except AppError:
                extra_warnings.append("Локальная LLM не смогла уточнить спецификацию; применены детерминированные правила")
                spec = baseline
        else:
            extra_warnings.append("Локальная LLM недоступна; применены детерминированные правила")

        _set_stage(transformation_id, ProcessingStage.TRANSFORMING)
        parsed = parse_workbook(source_path, spec, data_type)
        if llm_used and not parsed.rows and not feedback:
            spec = baseline
            parsed = parse_workbook(source_path, spec, data_type)
            llm_used = False
            extra_warnings.append("Спецификация LLM не дала результата; использована базовая спецификация")

        _set_stage(transformation_id, ProcessingStage.VALIDATING)
        validation = validate_result(parsed, spec)
        validation["warnings"] = list(dict.fromkeys(validation["warnings"] + extra_warnings))

        _set_stage(transformation_id, ProcessingStage.EXPORTING)
        version = previous_version + 1
        result_path = source_path.parent / f"result-v{version}.xlsx"
        export_result(result_path, parsed.rows, spec, validation)
        preview = [{key: value for key, value in row.items() if key != "_source_refs"} for row in parsed.rows[:100]]

        with connection() as db:
            db.execute(
                """
                INSERT INTO transformation_versions
                  (transformation_id, version, feedback, profile_json, spec_json, result_path,
                   preview_json, validation_json, processed_rows, unprocessed_rows, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transformation_id,
                    version,
                    feedback,
                    json_dump(profile),
                    spec.model_dump_json(),
                    str(result_path),
                    json_dump(preview),
                    json_dump(validation),
                    len(parsed.rows),
                    len(parsed.unprocessed_rows),
                    utc_now(),
                ),
            )
            db.execute(
                """
                UPDATE transformations
                SET status = 'COMPLETED', current_stage = 'COMPLETED', processed_rows = ?,
                    unprocessed_rows = ?, control_status = ?, controls_json = ?, warnings_json = ?,
                    error_message = NULL, current_version = ?, llm_used = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    len(parsed.rows),
                    len(parsed.unprocessed_rows),
                    validation["status"],
                    json_dump({
                        "calculated_sums": validation["calculated_sums"],
                        "control_comparisons": validation["control_comparisons"],
                        "missing_required": validation["missing_required"],
                    }),
                    json_dump(validation["warnings"]),
                    version,
                    int(llm_used),
                    utc_now(),
                    transformation_id,
                ),
            )
        record_audit(
            "TRANSFORMATION_COMPLETED",
            user_id=user_id,
            entity_type="TRANSFORMATION",
            entity_id=transformation_id,
            details={"version": version, "processedRows": len(parsed.rows), "llmUsed": llm_used},
        )
    except Exception as error:
        message = error.message if isinstance(error, AppError) else "Ошибка обработки файла"
        with connection() as db:
            db.execute(
                """
                UPDATE transformations SET status = 'FAILED', current_stage = 'FAILED',
                    error_message = ?, updated_at = ? WHERE id = ?
                """,
                (message, utc_now(), transformation_id),
            )
        record_audit(
            "TRANSFORMATION_FAILED",
            user_id=user_id,
            entity_type="TRANSFORMATION",
            entity_id=transformation_id,
            outcome="FAILURE",
            details={"message": message},
        )

