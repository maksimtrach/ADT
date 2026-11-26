from __future__ import annotations

import shutil
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from backend.app.api.dependencies import current_user
from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import connection, json_load, record_audit, utc_now
from backend.app.models.domain import (
    DataType,
    PreviewResponse,
    RetryInput,
    TransformationDetail,
    TransformationSummary,
    UserPublic,
    UserRole,
)
from backend.app.services.file_profiler import validate_xlsx
from backend.app.services.account_structure import SERVICE_COLUMNS
from backend.app.services.pipeline import process_transformation
from backend.app.services.planner import LLMPlanner
from backend.app.services.feedback_rules import explicit_column_feedback
from backend.app.services.processing_log import create_processing_run, record_run_event
from backend.app.services.source_uploads import consume_upload, discard_upload, owned_upload, source_preview, stage_upload
from backend.app.services.templates import TEMPLATES
from backend.app.services.document_profiles import PROFILES, profile_for


router = APIRouter(prefix="/transformations", tags=["transformations"])


def _owned_row(transformation_id: str, user: UserPublic):
    with connection() as db:
        row = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
    if not row or (row["user_id"] != user.id and user.role != UserRole.ADMIN):
        raise AppError(404, "Преобразование не найдено", "NOT_FOUND")
    return row


def _summary(row) -> TransformationSummary:
    data = dict(row)
    data["version"] = data.pop("current_version")
    return TransformationSummary.model_validate(data)


def _totals_wording(value):
    """Display historical diagnostics with current terminology, without rewriting logs."""
    if isinstance(value, str):
        return re.sub(r"([Кк])онтрольн(\w*) (?=сумм|строк)",
                      lambda m: ("И" if m[1] == "К" else "и") + "тогов" + m[2] + " ", value)
    if isinstance(value, list):
        return [_totals_wording(item) for item in value]
    if isinstance(value, dict):
        return {key: _totals_wording(item) for key, item in value.items()}
    return value


def _detail(row) -> TransformationDetail:
    data = dict(row)
    data["version"] = data.pop("current_version")
    data["warnings"] = json_load(data.pop("warnings_json"), [])
    data["controls"] = json_load(data.pop("controls_json"), {})
    data["llm_used"] = bool(data["llm_used"])
    from backend.app.services.accept_errors import latest_diagnostics, arithmetic_failures
    diagnostic_spec, validation = latest_diagnostics(data["id"])
    data["validation_details"] = validation
    data["can_accept_errors"] = bool(
        data["status"] == "FAILED" and diagnostic_spec and arithmetic_failures(validation)
        and not validation.get("numeric_errors") and not validation.get("missing_required")
        and not validation.get("unprocessed_row_numbers") and not validation.get("ambiguous_account_rows")
    )
    with connection() as db:
        latest_run = db.execute(
            """
            SELECT id, started_at, finished_at FROM transformation_runs
            WHERE transformation_id = ?
            ORDER BY target_version DESC, started_at DESC, rowid DESC
            LIMIT 1
            """,
            (data["id"],),
        ).fetchone()
        recognized = db.execute(
            """
            SELECT details_json FROM transformation_run_events
            WHERE run_id = ? AND event_type = 'STRUCTURE_RECOGNIZED'
            ORDER BY id DESC
            LIMIT 1
            """,
            (latest_run["id"],),
        ).fetchone() if latest_run else None
        failed = db.execute(
            """
            SELECT details_json FROM transformation_run_events
            WHERE run_id = ? AND event_type = 'PROCESSING_FAILED'
            ORDER BY id DESC
            LIMIT 1
            """,
            (latest_run["id"],),
        ).fetchone() if latest_run else None
        progress_events = db.execute(
            "SELECT event_type, stage, CASE WHEN event_type IN "
            "('PROCESSING_FAILED', 'PROCESSING_CANCELLED', 'HYPOTHESIS_EVALUATION_STARTED') "
            "THEN details_json ELSE NULL END AS details_json "
            "FROM transformation_run_events WHERE run_id=? ORDER BY id",
            (latest_run["id"],),
        ).fetchall() if latest_run else []
        correction_events = db.execute(
            "SELECT event_type, details_json FROM transformation_run_events "
            "WHERE run_id=? AND event_type IN ('STRUCTURE_RECOGNIZED', 'LLM_RESPONSE') ORDER BY id",
            (latest_run["id"],),
        ).fetchall() if latest_run and data["status"] == "FAILED" else []
    from backend.app.services.correction_structure import correction_structure
    from backend.app.services.progress import progress_details
    data.update(progress_details(latest_run, row, progress_events))
    data["recognized_structure"] = json_load(recognized["details_json"], None) if recognized else None
    data["correction_structure"] = correction_structure(correction_events, data["template_id"]) if correction_events else None
    failure = json_load(failed["details_json"], None) if failed else None
    data["error_details"] = ({
        "stage": failure.get("stage"), "code": failure.get("code", "PROCESSING_ERROR"),
        "reason": failure.get("reason", data.get("error_message") or "Ошибка обработки файла"),
        "details": failure.get("details", []), "recommendation": failure.get("recommendation"),
    } if failure else None)
    for key in ("error_message", "error_details", "warnings", "validation_details"):
        data[key] = _totals_wording(data.get(key))
    return TransformationDetail.model_validate(data)


@router.get("/templates")
def templates(user: UserPublic = Depends(current_user)):
    return {"data": list(TEMPLATES.values())}


@router.get("/document-profiles")
def document_profiles(user: UserPublic = Depends(current_user)):
    return {"data": [profile.public() for profile in PROFILES]}


@router.post("/source-uploads")
async def create_source_upload(file: UploadFile = File(...), user: UserPublic = Depends(current_user)):
    return {"data": await stage_upload(file, user.id)}


@router.get("/source-uploads/{upload_id}/preview")
def get_source_preview(upload_id: str, sheet: str | None = None, user: UserPublic = Depends(current_user)):
    return {"data": source_preview(upload_id, user.id, sheet)}


@router.delete("/source-uploads/{upload_id}", status_code=204)
def delete_source_upload(upload_id: str, user: UserPublic = Depends(current_user)):
    discard_upload(upload_id, user.id)


@router.post("")
async def create_transformation(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(None),
    upload_id: str | None = Form(None),
    data_type: DataType = Form(...),
    template_id: str = Form(...),
    user: UserPublic = Depends(current_user),
):
    if (file is None) == (upload_id is None):
        raise AppError(422, "Укажите исходный файл или идентификатор предварительной загрузки", "INVALID_UPLOAD")
    staged = owned_upload(upload_id, user.id)[1] if upload_id else None
    original_name = staged["filename"] if staged else Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".xlsx":
        raise AppError(400, "Поддерживаются только файлы XLSX", "UNSUPPORTED_FILE")
    template = TEMPLATES.get(template_id)
    if not template or template.data_type != data_type:
        raise AppError(400, "Выбран несовместимый целевой формат", "INVALID_TEMPLATE")
    profile_for(data_type, template_id)

    transformation_id = str(uuid4())
    directory = settings.storage_path / transformation_id
    directory.mkdir(parents=True, exist_ok=False)
    source_path = directory / "source.xlsx"
    staged_directory: Path | None = None
    size = 0
    try:
        if upload_id:
            staged_directory, staged = consume_upload(upload_id, user.id, source_path)
            size = staged["size_bytes"]
        else:
            with source_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise AppError(413, "Файл превышает допустимый размер", "FILE_TOO_LARGE")
                    destination.write(chunk)
            validate_xlsx(source_path, settings.max_upload_bytes)

        now = utc_now()
        with connection() as db:
            db.execute(
                """
                INSERT INTO transformations
                  (id, user_id, source_filename, source_path, data_type, template_id,
                   status, current_stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 'QUEUED', ?, ?)
                """,
                (transformation_id, user.id, original_name, str(source_path), data_type.value, template_id, now, now),
            )
            row = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
    except Exception:
        if staged_directory and source_path.is_file():
            try:
                source_path.replace(staged_directory / "source.xlsx")
            except OSError:
                pass
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        if file:
            await file.close()
    if staged_directory:
        shutil.rmtree(staged_directory, ignore_errors=True)
    record_audit(
        "TRANSFORMATION_CREATED",
        user_id=user.id,
        entity_type="TRANSFORMATION",
        entity_id=transformation_id,
        details={"dataType": data_type.value, "sizeBytes": size},
    )
    run_id = create_processing_run(transformation_id, 1)
    background_tasks.add_task(process_transformation, transformation_id, None, run_id)
    return {"data": _detail(row)}


@router.get("")
def list_transformations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserPublic = Depends(current_user),
):
    offset = (page - 1) * page_size
    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM transformations WHERE user_id = ?", (user.id,)).fetchone()[0]
        rows = db.execute(
            "SELECT * FROM transformations WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user.id, page_size, offset),
        ).fetchall()
    return {"data": [_summary(row) for row in rows], "meta": {"page": page, "pageSize": page_size, "total": total}}


@router.get("/{transformation_id}")
def get_transformation(transformation_id: str, user: UserPublic = Depends(current_user)):
    return {"data": _detail(_owned_row(transformation_id, user))}


@router.get("/{transformation_id}/memo")
def processing_memo(transformation_id: str, user: UserPublic = Depends(current_user)):
    _owned_row(transformation_id, user)
    with connection() as db:
        run = db.execute(
            """
            SELECT * FROM transformation_runs
            WHERE transformation_id = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (transformation_id,),
        ).fetchone()
        if not run:
            raise AppError(404, "Техническое мемо для этого преобразования отсутствует", "MEMO_NOT_AVAILABLE")
        events = db.execute(
            """
            SELECT id, stage, event_type, level, details_json, created_at
            FROM transformation_run_events
            WHERE run_id = ?
            ORDER BY id
            """,
            (run["id"],),
        ).fetchall()
    return {
        "data": {
            "run": dict(run),
            "events": [
                {
                    "id": event["id"],
                    "stage": event["stage"],
                    "event_type": event["event_type"],
                    "level": event["level"],
                    "details": json_load(event["details_json"], {}),
                    "created_at": event["created_at"],
                }
                for event in events
            ],
        }
    }


@router.get("/{transformation_id}/preview")
def preview(transformation_id: str, user: UserPublic = Depends(current_user)):
    row = _owned_row(transformation_id, user)
    if row["current_version"] < 1:
        raise AppError(409, "Результат еще не сформирован", "RESULT_NOT_READY")
    with connection() as db:
        version = db.execute(
            "SELECT * FROM transformation_versions WHERE transformation_id = ? AND version = ?",
            (transformation_id, row["current_version"]),
        ).fetchone()
    spec = json_load(version["spec_json"], {})
    columns = spec.get("target_columns", [])
    existing_keys = {column.get("key") for column in columns}
    columns = [
        *columns,
        *(column.model_dump(mode="json") for column in SERVICE_COLUMNS if column.key not in existing_keys),
    ]
    payload = PreviewResponse(
        columns=columns,
        rows=json_load(version["preview_json"], []),
        total=version["processed_rows"],
        version=version["version"],
    )
    return {"data": payload}


@router.get("/{transformation_id}/download")
def download(transformation_id: str, user: UserPublic = Depends(current_user)):
    row = _owned_row(transformation_id, user)
    if row["current_version"] < 1:
        raise AppError(409, "Результат еще не сформирован", "RESULT_NOT_READY")
    with connection() as db:
        version = db.execute(
            "SELECT result_path FROM transformation_versions WHERE transformation_id = ? AND version = ?",
            (transformation_id, row["current_version"]),
        ).fetchone()
    path = Path(version["result_path"])
    if not path.is_file() or path.parent.resolve() != Path(row["source_path"]).parent.resolve():
        raise AppError(404, "Файл результата не найден", "NOT_FOUND")
    record_audit("RESULT_DOWNLOADED", user_id=user.id, entity_type="TRANSFORMATION", entity_id=transformation_id)
    filename = f"{Path(row['source_filename']).stem}-result-v{row['current_version']}.xlsx"
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)


@router.post("/{transformation_id}/cancel")
def cancel(transformation_id: str, user: UserPublic = Depends(current_user)):
    row = _owned_row(transformation_id, user)
    if row["status"] == "CANCELLED":
        return {"data": _detail(row)}
    if row["status"] not in ("QUEUED", "RUNNING"):
        raise AppError(409, "Преобразование уже завершено", "TRANSFORMATION_NOT_ACTIVE")

    run = None
    with connection() as db:
        changed = db.execute(
            """
            UPDATE transformations
            SET status = 'CANCELLED', current_stage = 'CANCELLED',
                error_message = NULL, updated_at = ?
            WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
            """,
            (utc_now(), transformation_id),
        )
        current = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        if changed.rowcount:
            run = db.execute(
                """
                SELECT * FROM transformation_runs
                WHERE transformation_id = ? AND status IN ('QUEUED', 'RUNNING')
                ORDER BY started_at DESC LIMIT 1
                """,
                (transformation_id,),
            ).fetchone()
            if run:
                db.execute(
                    "UPDATE transformation_runs SET status = 'CANCELLED', finished_at = ? WHERE id = ?",
                    (utc_now(), run["id"]),
                )

    if not changed.rowcount:
        if current["status"] == "CANCELLED":
            return {"data": _detail(current)}
        raise AppError(409, "Преобразование уже завершено", "TRANSFORMATION_NOT_ACTIVE")

    if run:
        record_run_event(
            run["id"],
            transformation_id,
            "CANCELLED",
            "PROCESSING_CANCELLED",
            {"reason": "Пользователь начал новое преобразование", "stage": row["current_stage"]},
            level="WARNING",
        )
    record_audit(
        "TRANSFORMATION_CANCELLED",
        user_id=user.id,
        entity_type="TRANSFORMATION",
        entity_id=transformation_id,
        details={"previousStatus": row["status"]},
    )
    return {"data": _detail(current)}


@router.post("/{transformation_id}/accept-errors")
def accept_errors(transformation_id: str, background_tasks: BackgroundTasks,
                  user: UserPublic = Depends(current_user)):
    from backend.app.models.domain import TransformationSpec
    from backend.app.services.accept_errors import latest_diagnostics, assess_for_acceptance
    row = _owned_row(transformation_id, user)
    if row["status"] != "FAILED":
        raise AppError(409, "Принять ошибки можно только после неуспешной обработки", "CANNOT_ACCEPT_ERRORS")
    spec_data, _ = latest_diagnostics(transformation_id)
    if not spec_data:
        raise AppError(409, "Не сохранены правила разбора файла", "CANNOT_ACCEPT_ERRORS")
    spec = TransformationSpec.model_validate(spec_data)
    plan = assess_for_acceptance(Path(row["source_path"]), spec, DataType(row["data_type"]))
    with connection() as db:
        changed = db.execute(
            "UPDATE transformations SET status='QUEUED', current_stage='QUEUED', error_message=NULL, updated_at=? "
            "WHERE id=? AND status='FAILED'", (utc_now(), transformation_id),
        )
        if not changed.rowcount:
            raise AppError(409, "Состояние обработки изменилось", "ALREADY_RUNNING")
    run_id = create_processing_run(transformation_id, row["current_version"] + 1)
    record_audit("TRANSFORMATION_ERRORS_ACCEPTED", user_id=user.id, entity_type="TRANSFORMATION",
                 entity_id=transformation_id, details={"validation": plan.validation})
    background_tasks.add_task(process_transformation, transformation_id, None, run_id, plan.spec)
    return {"data": _detail(_owned_row(transformation_id, user))}


@router.post("/{transformation_id}/retry")
def retry(
    transformation_id: str,
    payload: RetryInput,
    background_tasks: BackgroundTasks,
    user: UserPublic = Depends(current_user),
):
    row = _owned_row(transformation_id, user)
    if row["status"] in ("QUEUED", "RUNNING"):
        raise AppError(409, "Обработка уже выполняется", "ALREADY_RUNNING")
    feedback = payload.feedback.strip() if payload.feedback else None
    if feedback and explicit_column_feedback(feedback) is None and not LLMPlanner().available():
        raise AppError(503, "Настроенные LLM недоступны", "LLM_UNAVAILABLE")
    with connection() as db:
        db.execute(
            "UPDATE transformations SET status = 'QUEUED', current_stage = 'QUEUED', error_message = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), transformation_id),
        )
        updated = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
    record_audit(
        "TRANSFORMATION_RETRY_REQUESTED",
        user_id=user.id,
        entity_type="TRANSFORMATION",
        entity_id=transformation_id,
        details={"hasFeedback": bool(feedback)},
    )
    run_id = create_processing_run(transformation_id, row["current_version"] + 1, feedback)
    background_tasks.add_task(process_transformation, transformation_id, feedback, run_id)
    return {"data": _detail(updated)}
