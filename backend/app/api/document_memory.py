from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.api.dependencies import current_user
from backend.app.core.errors import AppError
from backend.app.db.database import connection, json_load, record_audit
from backend.app.models.domain import DataType, TransformationSpec, UserPublic, UserRole
from backend.app.services.document_memory import MAX_PACKAGE_BYTES, MemoryCase, MemoryPackage, MemoryStore
from backend.app.services.hypothesis_planner import SourceStructure
from backend.app.services.memory_planner import capture_card


router = APIRouter(prefix="/document-memory", tags=["document-memory"])


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    status: Literal["draft", "approved", "disabled"]
    scope: Literal["private", "shared"] = "private"


def _record_save_failure(user: UserPublic, transformation_id: str, code: str, **details):
    record_audit(
        "MEMORY_SAVE_FAILED", user_id=user.id, entity_type="TRANSFORMATION",
        entity_id=transformation_id, outcome="FAILURE", details={"code": code, **details},
    )


@router.get("")
def list_memory(user: UserPublic = Depends(current_user)):
    return {"data": MemoryStore().visible(user.id, user.role == UserRole.ADMIN)}


@router.get("/cases")
def list_cases(user: UserPublic = Depends(current_user)):
    cases = [c for c in MemoryStore().read().cases if c.owner_id == user.id or user.role == UserRole.ADMIN]
    return {"data": list(reversed(cases[-100:]))}


@router.get("/export")
def export_memory(user: UserPublic = Depends(current_user)):
    package = MemoryStore().export(user.id, user.role == UserRole.ADMIN)
    record_audit("MEMORY_EXPORTED", user_id=user.id, details={"cards": len(package.cards)})
    return Response(package.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="document-memory.json"'})


@router.post("/import")
async def import_memory(file: UploadFile = File(...), user: UserPublic = Depends(current_user)):
    try:
        payload = await file.read(MAX_PACKAGE_BYTES + 1)
    finally:
        await file.close()
    if len(payload) > MAX_PACKAGE_BYTES:
        raise AppError(413, "Пакет памяти превышает 20 МБ", "MEMORY_TOO_LARGE")
    try:
        package = MemoryPackage.model_validate_json(payload)
    except ValidationError as error:
        raise AppError(422, "Некорректный пакет памяти или неподдерживаемая версия", "MEMORY_INVALID") from error
    result = MemoryStore().import_package(package, user.id)
    record_audit("MEMORY_IMPORTED", user_id=user.id, details=result)
    return {"data": result}


@router.post("/from-transformation/{transformation_id}")
def save_transformation(transformation_id: str, user: UserPublic = Depends(current_user)):
    from pathlib import Path
    with connection() as db:
        transformation = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        if not transformation or (transformation["user_id"] != user.id and user.role != UserRole.ADMIN):
            raise AppError(404, "Преобразование не найдено", "NOT_FOUND")
        if transformation["status"] != "COMPLETED" or transformation["data_type"] != DataType.TRIAL_BALANCE:
            raise AppError(409, "Сохраняется только завершённая общая ОСВ", "MEMORY_NOT_READY")
        version = db.execute("SELECT * FROM transformation_versions WHERE transformation_id = ? AND version = ?",
                             (transformation_id, transformation["current_version"])).fetchone()
    if not version:
        raise AppError(409, "Версия результата отсутствует", "MEMORY_NOT_READY")
    spec = TransformationSpec.model_validate(json_load(version["spec_json"], {}))
    path = Path(transformation["source_path"])
    if not path.is_file():
        raise AppError(409, "Для проверки подхода необходим исходный файл", "MEMORY_SOURCE_MISSING")
    source = SourceStructure(path)
    try:
        assessment = source.evaluate(spec, DataType.TRIAL_BALANCE, region_start=spec.data_start_row)
    finally:
        source.close()
    if not assessment.eligible:
        _record_save_failure(user, transformation_id, "MEMORY_NOT_VERIFIED")
        raise AppError(422, "Подход не прошёл повторную проверку исходных данных", "MEMORY_NOT_VERIFIED")
    # A completed transformation can still contain a layout that the portable
    # memory schema deliberately cannot replay (for example dynamic analytic
    # levels). Convert validation/serialization failures into a user-facing
    # response instead of leaking a 500 that the UI can only show as
    # «Ошибка запроса».
    try:
        card = capture_card(path, spec, assessment.validation, len(assessment.parsed.rows))
    except (ValidationError, ValueError, TypeError, OSError) as error:
        _record_save_failure(user, transformation_id, "MEMORY_UNSUPPORTED", error_type=type(error).__name__)
        raise AppError(
            422,
            "Подход нельзя сохранить в память версии 1: поддерживаются плоская общая ОСВ "
            "и дерево счетов без динамических уровней аналитики. Сохраните уточнённый "
            "подход после упрощения структуры или используйте новый профиль документа.",
            "MEMORY_UNSUPPORTED",
        ) from error
    if card is None:
        _record_save_failure(user, transformation_id, "MEMORY_UNSUPPORTED")
        raise AppError(422, "Этот подход содержит иерархию аналитик, подстановки или неподдерживаемую шапку. "
                       "Память версии 1 поддерживает плоскую общую ОСВ и дерево счетов", "MEMORY_UNSUPPORTED")
    try:
        saved = MemoryStore().save_case(MemoryCase(owner_id=user.id, transformation_id=transformation_id,
            run_id="manual", outcome="resolved", reason="Сохранено пользователем после повторной проверки"), card)
    except Exception as error:
        code = error.code if isinstance(error, AppError) else "MEMORY_WRITE_FAILED"
        _record_save_failure(user, transformation_id, code, error_type=type(error).__name__)
        raise
    record_audit("MEMORY_SAVED", user_id=user.id, entity_type="MEMORY", entity_id=str(saved.id))
    return {"data": saved}


@router.post("/{card_id}/status")
def change_status(card_id: UUID, payload: StatusChange, user: UserPublic = Depends(current_user)):
    record = MemoryStore().set_status(card_id, payload.revision, payload.status, payload.scope,
                                     user.id, user.role == UserRole.ADMIN)
    record_audit("MEMORY_STATUS_CHANGED", user_id=user.id, entity_type="MEMORY", entity_id=str(card_id),
                 details=payload.model_dump())
    return {"data": record}
