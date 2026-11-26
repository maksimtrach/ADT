import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import initialize_database
from backend.app.main import app
from backend.app.services import hypothesis_planner
from backend.app.services.document_profiles import DocumentProfile
from backend.app.services.planner import LLMPlanner
from backend.tests.test_api import add_user
from backend.tests.test_name_column_quality import make_book


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    monkeypatch.setattr(settings, "document_memory_enabled", False)
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    path = tmp_path / "source.xlsx"
    make_book(path)
    return path


def upload(client, path):
    client.post("/api/auth/login", json={
        "email": "test@example.test", "password": "TestPassword123!"})
    with path.open("rb") as source:
        response = client.post("/api/transformations", files={"file": (path.name, source)},
                               data={"data_type": "TRIAL_BALANCE",
                                     "template_id": "trial_balance_flat_v1"})
    return response.json()["data"]["id"]


@pytest.mark.parametrize("mode", ["rules", "choice"])
def test_winner_is_not_parsed_again_through_api(tmp_path, monkeypatch, mode):
    path = prepare(tmp_path, monkeypatch)
    original = hypothesis_planner.choose_plan

    def choose(*args, **kwargs):
        selection = original(*args, **kwargs)
        if mode == "choice":
            selection.confident = False
        return selection

    monkeypatch.setattr(hypothesis_planner, "choose_plan", choose)
    monkeypatch.setattr(LLMPlanner, "choose_candidate", lambda self, selection, **kw: selection.best)
    monkeypatch.setattr(DocumentProfile, "parse", lambda *a, **kw: pytest.fail("Повторный разбор победителя"))
    with TestClient(app) as client:
        tid = upload(client, path)
        detail = client.get(f"/api/transformations/{tid}").json()["data"]
        assert detail["status"] == "COMPLETED", detail
        memo = client.get(f"/api/transformations/{tid}/memo").json()["data"]
        checked = [e for e in memo["events"] if e["event_type"] in
                   ("BASELINE_CHECKED", "CANDIDATE_VALIDATED")]
        assert len(checked) == 2
        assert all(e["details"]["reused"] for e in checked)


@pytest.mark.parametrize("mode", ["choice", "recovery"])
@pytest.mark.parametrize("code", ["LLM_TIMEOUT", "LLM_UNAVAILABLE", "INVALID_LLM_RESPONSE"])
def test_llm_error_reason_survives_api(tmp_path, monkeypatch, mode, code):
    path = prepare(tmp_path, monkeypatch)
    original = hypothesis_planner.choose_plan

    def choose(*args, **kwargs):
        if mode == "recovery":
            raise AppError(422, "Шапка не распознана", "STRUCTURE_NOT_RECOGNIZED")
        selection = original(*args, **kwargs)
        selection.confident = False
        return selection

    def fail(*args, **kwargs):
        raise AppError(504, "Модель test: ожидание 10 с", code)

    monkeypatch.setattr(hypothesis_planner, "choose_plan", choose)
    monkeypatch.setattr(LLMPlanner, "choose_candidate", fail)
    monkeypatch.setattr(LLMPlanner, "recognize_structure", fail)
    with TestClient(app) as client:
        tid = upload(client, path)
        detail = client.get(f"/api/transformations/{tid}").json()["data"]
        assert detail["status"] == "FAILED", detail
        error = detail["error_details"]
        assert error["code"] == code
        assert error["reason"] == "Модель test: ожидание 10 с"
        assert "колон" not in error["recommendation"].lower()
