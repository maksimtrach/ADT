from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.auth.security import hash_password
from backend.app.core.config import settings
from backend.app.db.database import connection, initialize_database, utc_now
from backend.app.main import app
from backend.app.services.planner import OllamaPlanner
from scripts.generate_test_files import OUTPUT, generate_all


def add_user(email: str, password: str, role: str) -> None:
    with connection() as db:
        db.execute(
            "INSERT INTO users (id, name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), role.title(), email, hash_password(password), role, utc_now()),
        )


def test_auth_transform_and_admin_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    monkeypatch.setattr(OllamaPlanner, "available", lambda self: False)
    initialize_database()
    add_user("user@example.test", "UserPassword123!", "USER")
    add_user("admin@example.test", "AdminPassword123!", "ADMIN")
    generate_all()

    with TestClient(app) as user_client:
        login = user_client.post("/api/auth/login", json={"email": "user@example.test", "password": "UserPassword123!"})
        assert login.status_code == 200
        assert login.json()["data"]["role"] == "USER"
        with (OUTPUT / "04-unusual-journal.xlsx").open("rb") as source:
            created = user_client.post(
                "/api/transformations",
                files={"file": ("journal.xlsx", source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"data_type": "JOURNAL", "template_id": "journal_flat_v1"},
            )
        assert created.status_code == 200
        transformation_id = created.json()["data"]["id"]
        detail = user_client.get(f"/api/transformations/{transformation_id}")
        assert detail.json()["data"]["status"] == "COMPLETED"
        assert detail.json()["data"]["processed_rows"] == 2
        assert detail.json()["data"]["llm_used"] is False
        preview = user_client.get(f"/api/transformations/{transformation_id}/preview")
        assert preview.status_code == 200
        assert preview.json()["data"]["rows"][0]["amount"] == 12500.5
        download = user_client.get(f"/api/transformations/{transformation_id}/download")
        assert download.status_code == 200
        assert download.content.startswith(b"PK")
        assert user_client.get("/api/audit-log").status_code == 403

    with TestClient(app) as admin_client:
        login = admin_client.post("/api/auth/login", json={"email": "admin@example.test", "password": "AdminPassword123!"})
        assert login.status_code == 200
        audit = admin_client.get("/api/audit-log")
        assert audit.status_code == 200
        actions = {item["action"] for item in audit.json()["data"]}
        assert "TRANSFORMATION_COMPLETED" in actions
        assert "RESULT_DOWNLOADED" in actions

