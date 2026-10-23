from __future__ import annotations

import shutil
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
from backend.app.services.pipeline import process_transformation
from backend.app.services.planner import LLMPlanner
from backend.app.services.feedback_rules import explicit_column_feedback
from backend.app.services.processing_log import create_processing_run
from backend.app.services.templates import TEMPLATES


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


def _detail(row) -> TransformationDetail:
    data = dict(row)
    data["version"] = data.pop("current_version")
    data["warnings"] = json_load(data.pop("warnings_json"), [])
    data["controls"] = json_load(data.pop("controls_json"), {})
    data["llm_used"] = bool(data["llm_used"])
    return TransformationDetail.model_validate(data)


@router.get("/templates")
def templates(user: UserPublic = Depends(current_user)):
    return {"data": list(TEMPLATES.values())}


@router.post("")
async def create_transformation(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    data_type: DataType = Form(...),
    template_id: str = Form(...),
    user: UserPublic = Depends(current_user),
):
    original_name = Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".xlsx":
        raise AppError(400, "Поддерживаются только файлы XLSX", "UNSUPPORTED_FILE")
    template = TEMPLATES.get(template_id)
    if not template or template.data_type != data_type:
        raise AppError(400, "Выбран несовместимый целевой формат", "INVALID_TEMPLATE")

    transformation_id = str(uuid4())
    directory = settings.storage_path / transformation_id
    directory.mkdir(parents=True, exist_ok=False)
    source_path = directory / "source.xlsx"
    size = 0
    try:
        with source_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise AppError(413, "Файл превышает допустимый размер", "FILE_TOO_LARGE")
                destination.write(chunk)
        validate_xlsx(source_path, settings.max_upload_bytes)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()

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
    payload = PreviewResponse(
        columns=spec.get("target_columns", []),
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
