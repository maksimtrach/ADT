from __future__ import annotations

from uuid import uuid4
from dataclasses import replace
from pathlib import Path
import json
import time

import pytest

from fastapi.testclient import TestClient

from backend.app.auth.security import hash_password
from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import connection, initialize_database, utc_now
from backend.app.main import app
from backend.app.services.planner import OllamaPlanner
from backend.app.services.pipeline import process_transformation
from backend.app.services.processing_log import create_processing_run
from scripts.generate_test_files import OUTPUT, generate_all
from backend.tests.test_hypothesis_planner import ambiguous_workbook
from backend.tests.test_structure_recovery import recovery_workbook, fake_planner, answer_layout


@pytest.mark.parametrize("failure_mode", ["incomplete", "unrecognized", "technical"])
def test_api_uses_llm_recovery_only_after_structural_failure(tmp_path, monkeypatch, failure_mode):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    planner, calls = fake_planner(monkeypatch, answer_layout())
    monkeypatch.setattr("backend.app.services.pipeline.LLMPlanner", lambda: planner)
    if failure_mode != "incomplete":
        def no_structure(*args, **kwargs):
            if failure_mode == "technical":
                raise RuntimeError("Техническая ошибка чтения")
            raise AppError(422, "Шапка не распознана", "STRUCTURE_NOT_RECOGNIZED")
        monkeypatch.setattr("backend.app.services.hypothesis_planner.choose_plan", no_structure)
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    path = tmp_path / "recovery.xlsx"
    recovery_workbook(path)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        with path.open("rb") as source:
            response = client.post("/api/transformations", files={"file": (path.name, source)},
                                   data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        tid = response.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{tid}").json()["data"]
        if failure_mode == "technical":
            assert detail["status"] == "FAILED"
            assert detail["error_details"]["stage"] == "PLANNING"
            assert detail["error_details"]["code"] == "PROCESSING_ERROR"
            assert detail["error_details"]["reason"] == "Внутренняя ошибка приложения на этапе обработки"
            assert detail["error_details"]["details"] == ["Тип технической ошибки: RuntimeError"]
            assert calls == []
            assert not list((tmp_path / "storage").rglob("result-v*.xlsx"))
            return
        assert detail["status"] == "COMPLETED", detail
        assert detail["llm_used"] is True
        assert detail["processed_rows"] == 3
        assert detail["controls"]["calculated_sums"]["opening_debit"] == 100
        structure = detail["recognized_structure"]
        assert structure["sheet_name"] == "ОСВ"
        assert structure["data_start_row"] == 2
        assert structure["data_end_row"] == 4
        account = next(field for field in structure["fields"] if field["key"] == "account")
        assert account == {
            "key": "account", "label": "Счет", "column": 1,
            "column_letter": "A", "source": "LLM",
        }
        assert calls == ["first"]
        memo = client.get(f"/api/transformations/{tid}/memo").json()["data"]
        assert any(e["event_type"] == "LLM_STRUCTURE_ACCEPTED" for e in memo["events"])
        assert not any(e["event_type"] == "LLM_SKIPPED" for e in memo["events"])


def test_2017_workbook_api_without_model(tmp_path, monkeypatch):
    path = Path("test-data/complex/ОСВ_2017_тест.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    for method in ("available", "refine", "choose_candidate", "recognize_structure"):
        monkeypatch.setattr(OllamaPlanner, method, lambda *a, **kw: pytest.fail("Здесь модель не нужна"))
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        started = time.perf_counter()
        with path.open("rb") as source:
            response = client.post("/api/transformations", files={"file": (path.name, source)},
                                   data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        tid = response.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{tid}").json()["data"]
        assert detail["status"] == "COMPLETED", detail
        assert detail["control_status"] == "WARNINGS"
        assert detail["processed_rows"] == 233
        assert detail["llm_used"] is False
        assert detail["controls"]["calculated_sums"] == {}
        assert detail["controls"]["row_classification"] == {"account": 163, "currency": 32, "unlabelled": 38}
        download = client.get(f"/api/transformations/{tid}/download")
        assert download.status_code == 200
        from io import BytesIO
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(download.content), data_only=True)
        assert workbook["Результат"].max_row == 234
        assert "Тип суммы" in [cell.value for cell in workbook["Результат"][1]]
        workbook.close()
        print(f"2017 workbook full API pipeline + export: {time.perf_counter() - started:.3f}s")



def add_user(email: str, password: str, role: str) -> None:
    with connection() as db:
        db.execute(
            "INSERT INTO users (id, name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), role.title(), email, hash_password(password), role, utc_now()),
        )


def test_cancel_active_transformation_is_persisted_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    initialize_database()
    add_user("test.test", "TestPassword123!", "USER")
    transformation_id = str(uuid4())
    directory = tmp_path / "storage" / transformation_id
    directory.mkdir(parents=True)
    source_path = directory / "source.xlsx"
    source_path.write_bytes(b"unused")
    now = utc_now()
    with connection() as db:
        user_id = db.execute("SELECT id FROM users WHERE email = ?", ("test.test",)).fetchone()["id"]
        db.execute(
            """
            INSERT INTO transformations
              (id, user_id, source_filename, source_path, data_type, template_id,
               status, current_stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (transformation_id, user_id, "source.xlsx", str(source_path), "TRIAL_BALANCE",
             "trial_balance_flat_v1", "RUNNING", "PLANNING", now, now),
        )
    run_id = create_processing_run(transformation_id, 1)
    with connection() as db:
        db.execute("UPDATE transformation_runs SET status = ? WHERE id = ?", ("RUNNING", run_id))

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test.test", "password": "TestPassword123!"})
        cancelled = client.post(f"/api/transformations/{transformation_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["data"]["status"] == "CANCELLED"
        assert cancelled.json()["data"]["current_stage"] == "CANCELLED"
        again = client.post(f"/api/transformations/{transformation_id}/cancel")
        assert again.status_code == 200
        memo = client.get(f"/api/transformations/{transformation_id}/memo").json()["data"]
        assert memo["run"]["status"] == "CANCELLED"
        assert [event["event_type"] for event in memo["events"]] == ["PROCESSING_CANCELLED"]

    process_transformation(transformation_id, run_id=run_id)
    with connection() as db:
        transformation = db.execute("SELECT * FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
        run = db.execute("SELECT * FROM transformation_runs WHERE id = ?", (run_id,)).fetchone()
        audit_count = db.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE entity_id = ? AND action = ?",
            (transformation_id, "TRANSFORMATION_CANCELLED"),
        ).fetchone()[0]
        version_count = db.execute(
            "SELECT COUNT(*) FROM transformation_versions WHERE transformation_id = ?", (transformation_id,),
        ).fetchone()[0]
    assert transformation["status"] == "CANCELLED"
    assert run["status"] == "CANCELLED"
    assert audit_count == 1
    assert version_count == 0


def test_unresolved_structure_is_not_exported_and_can_be_clarified(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")

    def abstain(*args, **kwargs):
        raise AppError(422, "Оба источника правдоподобны", "STRUCTURE_AMBIGUOUS")

    monkeypatch.setattr(OllamaPlanner, "choose_candidate", abstain)
    monkeypatch.setattr(OllamaPlanner, "available", lambda *args: pytest.fail("Не нужна проверка модели"))
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    path = tmp_path / "ambiguous.xlsx"
    ambiguous_workbook(path)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        with path.open("rb") as source:
            response = client.post("/api/transformations", files={"file": (path.name, source)},
                                   data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        transformation_id = response.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["status"] == "FAILED"
        assert detail["version"] == 0
        assert "Брать счета из колонки C" in detail["error_message"]
        assert detail["error_details"]["stage"] == "PLANNING"
        assert detail["error_details"]["code"] == "STRUCTURE_AMBIGUOUS"
        assert "Уточните" in detail["error_details"]["recommendation"]
        assert not list((tmp_path / "storage").rglob("result-v*.xlsx"))
        response = client.post(f"/api/transformations/{transformation_id}/retry",
                               json={"feedback": "Брать счета из колонки C"})
        assert response.status_code == 200
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["status"] == "COMPLETED"
        assert detail["processed_rows"] == 2
        assert detail["llm_used"] is False


def test_auth_transform_and_admin_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    monkeypatch.setattr(OllamaPlanner, "available", lambda self: False)
    from backend.app.api.transformations import _detail
    from backend.app.services import pipeline
    record_event = pipeline.record_run_event
    live_operations = []

    def capture_progress(run_id, tid, stage, kind, details, **kwargs):
        record_event(run_id, tid, stage, kind, details, **kwargs)
        if kind == "HYPOTHESIS_EVALUATION_STARTED":
            with connection() as db:
                row = db.execute("SELECT * FROM transformations WHERE id=?", (tid,)).fetchone()
            operation = _detail(row).current_operation
            assert operation.endswith(f"проверка гипотезы {details['hypothesis_number']} из {details['total']}")
            live_operations.append(operation)

    monkeypatch.setattr(pipeline, "record_run_event", capture_progress)
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
        preview_data = preview.json()["data"]
        assert preview_data["rows"][0]["amount"] == 12500.5
        assert [column["key"] for column in preview_data["columns"]][-4:] == [
            "parent_account", "account_level", "row_type", "parent_source_row",
        ]
        assert preview_data["rows"][0]["parent_account"] is None
        assert preview_data["rows"][0]["account_level"] is None
        assert preview_data["rows"][0]["row_type"] is None
        assert preview_data["rows"][0]["parent_source_row"] is None
        memo = user_client.get(f"/api/transformations/{transformation_id}/memo")
        assert memo.status_code == 200
        memo_data = memo.json()["data"]
        assert memo_data["run"]["status"] == "COMPLETED"
        event_types = [event["event_type"] for event in memo_data["events"]]
        progress_types = {"HYPOTHESES_GENERATION_STARTED", "HYPOTHESES_PREPARATION_STARTED",
                          "HYPOTHESIS_EVALUATION_STARTED", "HYPOTHESES_SELECTION_STARTED"}
        progress = [event for event in memo_data["events"] if event["event_type"] in progress_types]
        assert progress[0]["event_type"] == "HYPOTHESES_GENERATION_STARTED"
        assert progress[1]["event_type"] == "HYPOTHESES_PREPARATION_STARTED"
        assert progress[-1]["event_type"] == "HYPOTHESES_SELECTION_STARTED"
        checks = [event["details"] for event in progress if event["event_type"] == "HYPOTHESIS_EVALUATION_STARTED"]
        assert checks
        assert len(live_operations) == len(checks)
        assert [check["hypothesis_number"] for check in checks] == list(range(1, len(checks) + 1))
        assert all(check["total"] == len(checks) for check in checks)
        assert [kind for kind in event_types if kind not in progress_types] == [
            "FILE_OPENED",
            "PROFILE_CREATED",
            "SOURCE_SAMPLE",
            "DOCUMENT_PROFILE_SELECTED",
            "HYPOTHESES_EVALUATED",
            "BASELINE_SPEC_CREATED",
            "STRUCTURE_RECOGNIZED",
            "BASELINE_CHECKED",
            "LLM_SKIPPED",
            "CANDIDATE_VALIDATED",
            "STRUCTURE_RECOGNIZED",
            "FINAL_SPEC_APPLIED",
            "PARSING_COMPLETED",
            "VALIDATION_COMPLETED",
            "RESULT_EXPORTED",
            "PROCESSING_COMPLETED",
        ]
        assert "source_path" not in memo.text
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


def test_real_workbook_upload_and_retry_skip_llm(tmp_path, monkeypatch):
    source_path = Path("test-data/complex/TB_2_lev_header.xlsx")
    if not source_path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")

    def no_llm(*args, **kwargs):
        pytest.fail("Для этого файла LLM и проверка доступности не должны вызываться")

    monkeypatch.setattr(OllamaPlanner, "available", no_llm)
    monkeypatch.setattr(OllamaPlanner, "refine", no_llm)
    monkeypatch.setattr(OllamaPlanner, "choose_candidate", no_llm)
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        started = time.perf_counter()
        with source_path.open("rb") as source:
            created = client.post(
                "/api/transformations",
                files={"file": (source_path.name, source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"},
            )
        assert created.status_code == 200
        transformation_id = created.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["status"] == "COMPLETED"
        assert detail["control_status"] == "PASSED"
        assert detail["processed_rows"] == 129
        assert detail["llm_used"] is False
        memo = client.get(f"/api/transformations/{transformation_id}/memo").json()["data"]
        assert not any(event["event_type"] == "LLM_REQUEST" for event in memo["events"])
        assert client.get(f"/api/transformations/{transformation_id}/download").content.startswith(b"PK")
        print(f"Real XLSX full API pipeline: {time.perf_counter() - started:.3f}s")
        # Simulate an old incorrect spec: a retry without feedback must rebuild it.
        with connection() as db:
            old = json.loads(db.execute(
                "SELECT spec_json FROM transformation_versions WHERE transformation_id=?", (transformation_id,)
            ).fetchone()["spec_json"])
            old["column_mapping"]["opening_debit"] = 4
            db.execute("UPDATE transformation_versions SET spec_json=? WHERE transformation_id=?",
                       (json.dumps(old), transformation_id))
        assert client.post(f"/api/transformations/{transformation_id}/retry", json={}).status_code == 200
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["version"] == 2
        assert detail["control_status"] == "PASSED"

        # A syntactically valid LLM reply with shifted money columns is rejected
        # by actual parsing, even when the model reports high confidence.
        monkeypatch.setattr("backend.app.services.pipeline.baseline_is_reliable", lambda spec, validation: False)
        monkeypatch.setattr(OllamaPlanner, "available", lambda self: True)

        def bad_choice(self, selection, **kwargs):
            candidate = selection.best.spec.model_copy(deep=True)
            candidate.column_mapping["opening_debit"] = 4
            candidate.confidence = 0.99
            return replace(selection.best, spec=candidate)

        monkeypatch.setattr(OllamaPlanner, "choose_candidate", bad_choice)
        assert client.post(f"/api/transformations/{transformation_id}/retry", json={}).status_code == 200
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["version"] == 3
        assert detail["control_status"] == "PASSED"
        assert detail["llm_used"] is False
        memo = client.get(f"/api/transformations/{transformation_id}/memo").json()["data"]
        assert any(event["event_type"] == "LLM_RESULT_FALLBACK" for event in memo["events"])


def test_short_workbook_and_explicit_mapping_feedback_without_llm(tmp_path, monkeypatch):
    source_path = Path("test-data/complex/ОСВ _тест2_кратк.xlsx")
    if not source_path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")

    def no_llm(*args, **kwargs):
        pytest.fail("В этом сценарии модель не требуется")

    for method in ("available", "refine", "choose_candidate"):
        monkeypatch.setattr(OllamaPlanner, method, no_llm)
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        started = time.perf_counter()
        with source_path.open("rb") as source:
            created = client.post("/api/transformations",
                files={"file": (source_path.name, source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        transformation_id = created.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["status"] == "COMPLETED"
        assert detail["control_status"] == "WARNINGS"
        assert detail["processed_rows"] == 132
        assert detail["llm_used"] is False
        print(f"Short workbook full API pipeline: {time.perf_counter() - started:.3f}s")
        with connection() as db:
            old = json.loads(db.execute("SELECT spec_json FROM transformation_versions WHERE transformation_id=?",
                                       (transformation_id,)).fetchone()["spec_json"])
            old["column_mapping"]["account"] = 1
            old["value_overrides"] = [{"target_field": "account", "from_row": 5, "to_row": None, "value": "WRONG"}]
            db.execute("UPDATE transformation_versions SET spec_json=? WHERE transformation_id=?", (json.dumps(old), transformation_id))
        retried = client.post(f"/api/transformations/{transformation_id}/retry",
                              json={"feedback": "Загрузи номера счетов из колонки C"})
        assert retried.status_code == 200
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["version"] == 2
        assert detail["status"] == "COMPLETED"
        preview = client.get(f"/api/transformations/{transformation_id}/preview").json()["data"]
        assert preview["rows"][0]["account"] == "01"
        assert preview["rows"][1]["account"] == "01.01"
        memo = client.get(f"/api/transformations/{transformation_id}/memo").json()["data"]
        assert any(event["event_type"] == "USER_MAPPING_APPLIED" for event in memo["events"])
        assert not any(event["event_type"] == "LLM_REQUEST" for event in memo["events"])
