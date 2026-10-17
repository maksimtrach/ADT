from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request, Response

from backend.app.api.dependencies import current_user
from backend.app.auth.security import SESSION_COOKIE, create_session, revoke_session, verify_password
from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import connection, record_audit
from backend.app.models.domain import LoginInput, UserPublic


router = APIRouter(prefix="/auth", tags=["auth"])
attempts: dict[str, deque[float]] = defaultdict(deque)


def _throttle_key(request: Request, email: str) -> str:
    source = f"{request.client.host if request.client else 'unknown'}:{email.lower()}"
    return hashlib.sha256(source.encode()).hexdigest()


@router.post("/login")
def login(payload: LoginInput, request: Request, response: Response):
    email = payload.email.strip().lower()
    key = _throttle_key(request, email)
    now = time.monotonic()
    queue = attempts[key]
    while queue and queue[0] < now - 900:
        queue.popleft()
    if len(queue) >= 10:
        raise AppError(429, "Слишком много попыток входа. Повторите позже", "LOGIN_THROTTLED")
    with connection() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(row["password_hash"], payload.password):
        queue.append(now)
        record_audit("LOGIN_FAILED", outcome="FAILURE", details={"identifier": hashlib.sha256(email.encode()).hexdigest()[:16]})
        raise AppError(401, "Неверный email или пароль", "INVALID_CREDENTIALS")
    attempts.pop(key, None)
    token = create_session(row["id"])
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    record_audit("LOGIN_SUCCEEDED", user_id=row["id"])
    return {"data": UserPublic.model_validate(dict(row))}


@router.get("/me")
def me(user: UserPublic = Depends(current_user)):
    return {"data": user}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: UserPublic = Depends(current_user)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    record_audit("LOGOUT", user_id=user.id)
    return response

