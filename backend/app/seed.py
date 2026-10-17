from __future__ import annotations

from uuid import uuid4

from backend.app.auth.security import hash_password
from backend.app.core.config import settings
from backend.app.db.database import connection, initialize_database, utc_now


def upsert_user(email: str, password: str, name: str, role: str) -> None:
    with connection() as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email.lower(),)).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET name = ?, password_hash = ?, role = ? WHERE id = ?",
                (name, hash_password(password), role, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO users (id, name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), name, email.lower(), hash_password(password), role, utc_now()),
            )


def seed() -> None:
    initialize_database()
    upsert_user(settings.demo_user_email, settings.demo_user_password, "Демо-пользователь", "USER")
    upsert_user(settings.demo_admin_email, settings.demo_admin_password, "Администратор", "ADMIN")
    print("Демонстрационные пользователи созданы или обновлены")


if __name__ == "__main__":
    seed()

