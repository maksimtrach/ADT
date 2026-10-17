from __future__ import annotations

from fastapi import Cookie, Depends

from backend.app.auth.security import SESSION_COOKIE, resolve_session
from backend.app.core.errors import AppError
from backend.app.models.domain import UserPublic, UserRole


def current_user(adt_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> UserPublic:
    if not adt_session:
        raise AppError(401, "Требуется вход", "UNAUTHENTICATED")
    row = resolve_session(adt_session)
    if not row:
        raise AppError(401, "Сессия недействительна", "UNAUTHENTICATED")
    return UserPublic.model_validate(row)


def admin_user(user: UserPublic = Depends(current_user)) -> UserPublic:
    if user.role != UserRole.ADMIN:
        raise AppError(403, "Недостаточно прав", "FORBIDDEN")
    return user

