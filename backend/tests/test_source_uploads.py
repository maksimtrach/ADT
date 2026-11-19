from __future__ import annotations

from io import BytesIO
import json
import os
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.core.config import settings
from backend.app.db.database import initialize_database
from backend.app.main import app
from backend.app.services.source_uploads import UPLOAD_TTL_SECONDS
from backend.tests.test_api import add_user


def workbook_bytes() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet["A1"] = "Счет"
    sheet["B1"] = "Наименование"
    sheet["A3"] = "01"
    sheet["B3"] = "Основные средства"
    sheet["A100"] = "100"
    sheet["A105"] = "105"
    second = book.create_sheet("Второй лист")
    second["A1"] = "Другой лист"
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    monkeypatch.setattr("backend.app.api.transformations.process_transformation", lambda *args: None)
    initialize_database()
    add_user("first@example.test", "TestPassword123!", "USER")
    add_user("second@example.test", "TestPassword123!", "USER")


def login(client, email):
    response = client.post("/api/auth/login", json={"email": email, "password": "TestPassword123!"})
    assert response.status_code == 200


def stage(client, payload):
    return client.post("/api/transformations/source-uploads", files={
        "file": ("source.xlsx", BytesIO(payload), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })


def test_source_preview_and_processing_reuse_exact_uploaded_file(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    payload = workbook_bytes()
    with TestClient(app) as client:
        login(client, "first@example.test")
        response = stage(client, payload)
        assert response.status_code == 200, response.text
        preview = response.json()["data"]
        upload_id = preview["upload_id"]
        assert preview["sheets"] == ["ОСВ", "Второй лист"]
        assert preview["sheet_name"] == "ОСВ"
        assert preview["columns"] == ["A", "B"]
        assert preview["total_rows"] == 105
        assert len(preview["rows"]) == 100
        assert preview["rows"][0] == {"row": 1, "cells": ["Счет", "Наименование"]}
        assert preview["rows"][1] == {"row": 2, "cells": [None, None]}
        assert preview["rows"][-1] == {"row": 100, "cells": ["100", None]}
        assert "105" not in str(preview["rows"])
        second = client.get(f"/api/transformations/source-uploads/{upload_id}/preview", params={"sheet": "Второй лист"})
        assert second.status_code == 200
        assert second.json()["data"]["rows"][0]["cells"] == ["Другой лист"]
        assert client.get(f"/api/transformations/source-uploads/{upload_id}/preview", params={"sheet": "Не существует"}).status_code == 404

        created = client.post("/api/transformations", data={
            "upload_id": upload_id, "data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1",
        })
        assert created.status_code == 200, created.text
        transformation_id = created.json()["data"]["id"]
        assert (settings.storage_path / transformation_id / "source.xlsx").read_bytes() == payload
        assert not (settings.storage_path / "_source_uploads" / upload_id).exists()
        assert client.get(f"/api/transformations/source-uploads/{upload_id}/preview").status_code == 404
        assert client.post("/api/transformations", data={
            "upload_id": upload_id, "data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1",
        }).status_code == 404


def test_source_upload_is_private_and_can_be_discarded(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with TestClient(app) as first, TestClient(app) as second:
        login(first, "first@example.test")
        login(second, "second@example.test")
        upload_id = stage(first, workbook_bytes()).json()["data"]["upload_id"]
        assert second.get(f"/api/transformations/source-uploads/{upload_id}/preview").status_code == 404
        assert second.delete(f"/api/transformations/source-uploads/{upload_id}").status_code == 404
        assert second.post("/api/transformations", data={
            "upload_id": upload_id, "data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1",
        }).status_code == 404
        assert first.get(f"/api/transformations/source-uploads/{upload_id}/preview").status_code == 200
        assert first.delete(f"/api/transformations/source-uploads/{upload_id}").status_code == 204
        assert first.get(f"/api/transformations/source-uploads/{upload_id}/preview").status_code == 404
        assert first.get("/api/transformations/source-uploads/not-a-uuid/preview").status_code == 404


def test_invalid_source_file_is_rejected_without_staging(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client, "first@example.test")
        response = stage(client, b"not an xlsx")
        assert response.status_code == 400
        root = settings.storage_path / "_source_uploads"
        assert not root.exists() or not list(root.iterdir())


def test_expired_upload_cannot_be_processed_and_is_cleaned_on_next_upload(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client, "first@example.test")
        upload_id = stage(client, workbook_bytes()).json()["data"]["upload_id"]
        directory = settings.storage_path / "_source_uploads" / upload_id
        metadata_path = directory / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["created_at"] = time.time() - UPLOAD_TTL_SECONDS - 10
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        assert client.get(f"/api/transformations/source-uploads/{upload_id}/preview").status_code == 410
        assert client.post("/api/transformations", data={
            "upload_id": upload_id, "data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1",
        }).status_code == 410
        old_time = time.time() - UPLOAD_TTL_SECONDS - 10
        os.utime(directory, (old_time, old_time))
        assert stage(client, workbook_bytes()).status_code == 200
        assert not directory.exists()
