from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.app.core.config import settings
from backend.app.db.database import connection, utc_now


SESSION_COOKIE = "adt_session"
password_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.session_ttl_hours)
    with connection() as db:
        db.execute(
            """
            INSERT INTO sessions (token_hash, user_id, expires_at, last_seen_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash(token), user_id, expires_at.isoformat(), now.isoformat(), now.isoformat()),
        )
    return token


def resolve_session(token: str):
    now = datetime.now(UTC)
    digest = token_hash(token)
    with connection() as db:
        row = db.execute(
            """
            SELECT u.id, u.name, u.email, u.role, s.expires_at
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (digest,),
        ).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) <= now:
            if row:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            return None
        db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (utc_now(), digest))
        return dict(row)


def revoke_session(token: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

