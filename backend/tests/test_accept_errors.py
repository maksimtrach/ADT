from io import BytesIO
from openpyxl import Workbook, load_workbook
from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import initialize_database, connection
from backend.app.main import app
from backend.app.services.planner import OllamaPlanner
from backend.tests.test_api import add_user


@pytest.mark.parametrize("bad_number", [False, True])
def test_accept_errors_preserves_diagnostics_and_rejects_invalid_data(tmp_path, monkeypatch, bad_number):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    monkeypatch.setattr(settings, "document_memory_enabled", False)
    def unavailable(*args, **kwargs):
        raise AppError(422, "Суммы не совпали", "INVALID_LLM_STRUCTURE")
    monkeypatch.setattr(OllamaPlanner, "recognize_structure", unavailable)
    monkeypatch.setattr(OllamaPlanner, "choose_candidate", unavailable)
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    book = Workbook()
    sheet = book.active
    sheet.append(["Счет", "Наименование счета", "Начало Дт", "Начало Кт", "Оборот Дт", "Оборот Кт", "Конец Дт", "Конец Кт"])
    sheet.append(["01", "ОС", 100, 0, "ошибка" if bad_number else 20, 10, 120, 0])
    sheet.append(["Итого", None, 999, 0, 20, 10, 120, 0])
    data = BytesIO()
    book.save(data)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        created = client.post("/api/transformations", files={"file": ("bad.xlsx", data.getvalue())},
                              data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        tid = created.json()["data"]["id"]
        before = client.get(f"/api/transformations/{tid}").json()["data"]
        assert before["status"] == "FAILED"
        if bad_number:
            assert before["can_accept_errors"] is False
            assert client.post(f"/api/transformations/{tid}/accept-errors").status_code == 409
            return
        assert before["can_accept_errors"] is True
        assert before["validation_details"]["row_balance"]["examples"] == [{"row": 2, "difference": -10}]
        def forbidden(*args, **kwargs):
            pytest.fail("Acceptance must not call LLM")
        monkeypatch.setattr(OllamaPlanner, "recognize_structure", forbidden)
        monkeypatch.setattr(OllamaPlanner, "choose_candidate", forbidden)
        response = client.post(f"/api/transformations/{tid}/accept-errors")
        assert response.status_code == 200, response.text
        after = client.get(f"/api/transformations/{tid}").json()["data"]
        assert after["status"] == "COMPLETED", after
        assert after["control_status"] == "FAILED"
        assert after["validation_details"]["accepted_with_errors"] is True
        assert after["validation_details"]["row_balance"]["examples"] == [{"row": 2, "difference": -10}]
        assert after["validation_details"]["control_comparisons"]["opening_debit"]["difference"] == -899
        assert after["error_details"] is None
        assert after["can_accept_errors"] is False
        result = client.get(f"/api/transformations/{tid}/download")
        exported = load_workbook(BytesIO(result.content))
        assert exported["Результат"].cell(2, 3).value == 100
        assert any(row[0].value == "Нарушение формулы: строка 2" for row in exported["Контроль"])
        assert client.post(f"/api/transformations/{tid}/accept-errors").status_code == 409
        with connection() as db:
            assert db.execute("SELECT count(*) FROM audit_logs WHERE entity_id=? AND action='TRANSFORMATION_ERRORS_ACCEPTED'", (tid,)).fetchone()[0] == 1
