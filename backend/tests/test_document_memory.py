from io import BytesIO
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import connection, initialize_database
from backend.app.main import app
from backend.app.models.domain import DataType
from backend.app.services.document_memory import MemoryCase, MemoryPackage, MemoryStore
from backend.app.services.hypothesis_planner import PlanSelection, SourceStructure
from backend.app.services.memory_planner import capture_card, memory_plan
from backend.app.services.structure_recovery import StructureLayout, build_recovered_spec
from backend.tests.test_api import add_user
from backend.tests.test_structure_recovery import recovery_workbook, answer_layout, fake_planner


def saved_card(tmp_path, owner="owner"):
    path = tmp_path / "source.xlsx"
    recovery_workbook(path)
    source = SourceStructure(path)
    try:
        spec = build_recovered_spec(StructureLayout(**answer_layout()["layout"]), source,
                                    DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
        result = source.evaluate(spec, DataType.TRIAL_BALANCE, region_start=1)
        assert result.eligible
        card = capture_card(path, spec, result.validation, len(result.parsed.rows))
    finally:
        source.close()
    assert card
    store = MemoryStore()
    store.save_case(MemoryCase(owner_id=owner, transformation_id="local-source-id", run_id="local-run",
                               outcome="resolved", reason="Проверено"), card)
    return path, card, store


def test_code_only_tree_card_roundtrip_and_replay(tmp_path, monkeypatch):
    path = tmp_path / "code-only-tree.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Счет", "Начало Дт", "Начало Кт", "Оборот Дт", "Оборот Кт", "Конец Дт", "Конец Кт"])
    sheet.append(["01", 100, 0, 20, 10, 110, 0])
    sheet.append(["01.01", 100, 0, 20, 10, 110, 0])
    sheet.append(["Итого", 100, 0, 20, 10, 110, 0])
    book.save(path)
    book.close()

    source = SourceStructure(path)
    try:
        layout = StructureLayout(
            sheet_name="ОСВ", header_start_row=1, header_end_row=1, data_start_row=2, data_end_row=4,
            row_mode="account_tree", column_mapping={"account": 1, **dict(zip((
                "opening_debit", "opening_credit", "turnover_debit", "turnover_credit",
                "closing_debit", "closing_credit",
            ), range(2, 8)))},
        )
        spec = build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
        assessed = source.evaluate(spec, DataType.TRIAL_BALANCE, region_start=2)
        assert assessed.eligible
        card = capture_card(path, spec, assessed.validation, len(assessed.parsed.rows))
    finally:
        source.close()

    assert card is not None
    assert card.row_mode == "account_tree"
    assert "account_name" not in card.column_mapping
    store = MemoryStore()
    store.save_case(MemoryCase(owner_id="owner", transformation_id="source", run_id="run",
                               outcome="resolved", reason="Проверено"), card)
    store.set_status(card.id, card.revision, "approved", "private", "owner")
    package = store.export("owner")

    monkeypatch.setattr(settings, "document_memory_path", tmp_path / "portable-memory")
    imported = MemoryStore()
    assert imported.import_package(package, "new-owner") == {"imported": 1, "skipped": 0}
    imported.set_status(card.id, card.revision, "approved", "private", "new-owner")
    result = memory_plan(path, "new-owner", card.template_id, lambda *args: None)
    assert result is not None
    assert result.spec.row_rules.account_tree
    assert not result.spec.row_rules.classify_account_rows
    assert result.validation["row_balance"]["failed"] == 0
    assert all(item["passed"] for item in result.validation["control_comparisons"].values())


def test_pipeline_auto_saves_nontrivial_code_tree_as_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "code-tree.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    initialize_database()
    add_user("tree@test.local", "TestPassword123!", "USER")
    path = tmp_path / "code-tree-upload.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Счет", "Начало Дт", "Начало Кт", "Оборот Дт", "Оборот Кт", "Конец Дт", "Конец Кт"])
    rows = [
        ("01", [100, 0, 20, 10, 110, 0], 0),
        ("01.01", [100, 0, 20, 10, 110, 0], 1),
        ("02", [0, 50, 10, 20, 0, 60], 0),
        ("02.01", [0, 50, 10, 20, 0, 60], 1),
    ]
    for account, amounts, indent in rows:
        sheet.append([account, *amounts])
        sheet.cell(sheet.max_row, 1).alignment = Alignment(indent=indent)
        sheet.row_dimensions[sheet.max_row].outlineLevel = indent
    sheet.append(["Итого", 100, 50, 30, 30, 110, 60])
    book.save(path)
    book.close()

    class NoLLM:
        def __getattr__(self, name):
            raise AssertionError(f"LLM не должна вызываться: {name}")

    monkeypatch.setattr("backend.app.services.pipeline.LLMPlanner", lambda: NoLLM())
    with TestClient(app) as client:
        login(client, "tree@test.local")
        response = client.post("/api/transformations", files={"file": (path.name, path.read_bytes())},
                               data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        assert response.status_code == 200, response.text
        transformation_id = response.json()["data"]["id"]
        detail = client.get(f"/api/transformations/{transformation_id}").json()["data"]
        assert detail["status"] == "COMPLETED", detail
        assert not detail["llm_used"]
        records = client.get("/api/document-memory").json()["data"]
        assert len(records) == 1
        assert records[0]["status"] == "draft"
        assert "account_name" not in records[0]["card"]["column_mapping"]
        events = client.get(f"/api/transformations/{transformation_id}/memo").json()["data"]["events"]
        assert any(event["event_type"] == "MEMORY_CASE_SAVED" for event in events)
        manual = client.post(f"/api/document-memory/from-transformation/{transformation_id}")
        assert manual.status_code == 200, manual.text
        assert len(client.get("/api/document-memory").json()["data"]) == 1

        monkeypatch.setattr("backend.app.api.document_memory.capture_card", lambda *args: None)
        failed = client.post(f"/api/document-memory/from-transformation/{transformation_id}")
        assert failed.status_code == 422
        assert failed.json()["error"]["code"] == "MEMORY_UNSUPPORTED"
        with connection() as db:
            audit = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
        assert audit["action"] == "MEMORY_SAVE_FAILED"
        assert audit["outcome"] == "FAILURE"


def test_transfer_requires_approval_and_rechecks_shifted_new_data(tmp_path, monkeypatch):
    path, card, store = saved_card(tmp_path)
    store.set_status(card.id, 1, "approved", "private", "owner")
    package = store.export("owner")
    assert "local-source-id" not in package.model_dump_json()
    assert "local-run" not in package.model_dump_json()
    assert "Валютные счета" not in package.model_dump_json()
    monkeypatch.setattr(settings, "document_memory_path", tmp_path / "another-install")
    new_store = MemoryStore()
    assert new_store.import_package(package, "new-user") == {"imported": 1, "skipped": 0}
    assert memory_plan(path, "new-user", card.template_id, lambda *a: None) is None
    new_store.set_status(card.id, 1, "approved", "private", "new-user")
    book = load_workbook(path)
    sheet = book.active
    sheet.insert_rows(1, 3)
    sheet.title = "Новая выгрузка"
    sheet["C5"] = 200
    sheet["G5"] = 210
    book.save(path)
    book.close()
    result = memory_plan(path, "new-user", card.template_id, lambda *a: None)
    assert result is not None
    assert result.spec.header_row == 4
    assert result.spec.data_end_row == 7
    assert result.parsed.rows[0]["opening_debit"] == 200
    assert len(result.parsed.rows) == 3
    assert new_store.read().records[0].successes == 1
    assert memory_plan(path, "other-user", card.template_id, lambda *a: None) is None


@pytest.mark.parametrize("change", ["arithmetic", "header", "formula", "extra_sheet", "totals"])
def test_memory_does_not_force_invalid_or_ambiguous_file(tmp_path, change):
    path, card, store = saved_card(tmp_path)
    store.set_status(card.id, 1, "approved", "private", "owner")
    book = load_workbook(path)
    if change == "arithmetic": book.active["G2"] = 999
    if change == "header": book.active["C1"] = "Иной показатель"
    if change == "formula": book.active["C2"] = "=100"
    if change == "extra_sheet": book.copy_worksheet(book.active)
    if change == "totals": book.active.append(["Итого", None, 900, 0, 20, 10, 910, 0])
    book.save(path)
    book.close()
    events = []
    assert memory_plan(path, "owner", card.template_id, lambda e, d: events.append(e)) is None
    if change == "extra_sheet": assert "MEMORY_AMBIGUOUS" in events
    if change in {"arithmetic", "formula", "totals"}: assert "MEMORY_REJECTED" in events


def test_import_is_atomic_and_versions_do_not_overwrite(tmp_path):
    _, card, store = saved_card(tmp_path)
    before = store.path.read_bytes()
    changed = card.model_copy(update={"description": "Другое содержание"})
    extra = card.model_copy(update={"id": uuid4()})
    with pytest.raises(AppError, match="Конфликт"):
        store.import_package(MemoryPackage(cards=[extra, changed]), "owner")
    assert store.path.read_bytes() == before
    store.set_status(card.id, 1, "approved", "private", "owner")
    updated = card.model_copy(update={"revision": 2})
    store.import_package(MemoryPackage(cards=[updated]), "owner")
    assert store.active("owner") == []
    assert store.import_package(MemoryPackage(cards=[updated]), "owner")["skipped"] == 1
    assert len(store.read().records) == 2


def test_conflicting_approved_mappings_require_clarification(tmp_path):
    path, card, store = saved_card(tmp_path)
    store.set_status(card.id, 1, "approved", "private", "owner")
    mapping = dict(card.column_mapping)
    for period in ("opening", "turnover", "closing"):
        debit, credit = f"{period}_debit", f"{period}_credit"
        mapping[debit], mapping[credit] = mapping[credit], mapping[debit]
    alternate = card.model_copy(update={"id": uuid4(), "column_mapping": mapping})
    store.import_package(MemoryPackage(cards=[alternate]), "owner")
    store.set_status(alternate.id, 1, "approved", "private", "owner")
    events = []
    assert memory_plan(path, "owner", card.template_id, lambda e, d: events.append(e)) is None
    assert "MEMORY_AMBIGUOUS" in events


def test_explicit_feedback_bypasses_memory_and_store_failure_is_nonfatal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "feedback.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    initialize_database()
    add_user("a@test.local", "TestPassword123!", "USER")
    path = tmp_path / "source.xlsx"
    recovery_workbook(path)
    planner, _ = fake_planner(monkeypatch, answer_layout())
    monkeypatch.setattr("backend.app.services.pipeline.LLMPlanner", lambda: planner)
    # A malformed library cannot make an otherwise successful conversion fail.
    settings.document_memory_path.mkdir()
    (settings.document_memory_path / "library.json").write_text("broken", encoding="utf-8")
    with TestClient(app) as client:
        login(client, "a@test.local")
        response = client.post("/api/transformations", files={"file": ("osv.xlsx", path.read_bytes())},
            data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        tid = response.json()["data"]["id"]
        assert client.get(f"/api/transformations/{tid}").json()["data"]["status"] == "COMPLETED"
        events = client.get(f"/api/transformations/{tid}/memo").json()["data"]["events"]
        assert any(e["event_type"] == "MEMORY_UNAVAILABLE" for e in events)
        def forbidden(*a, **kw):
            pytest.fail("Явное исправление не должно обращаться к памяти или LLM")
        monkeypatch.setattr("backend.app.services.pipeline.memory_plan", forbidden)
        monkeypatch.setattr(planner, "recognize_structure", forbidden)
        monkeypatch.setattr(planner, "refine", forbidden)
        response = client.post(f"/api/transformations/{tid}/retry", json={"feedback":
            "Бери номер счета из колонки A, наименование счета из колонки B, "
            "остатки по Дт/Кт колонки C и D, обороты Дт/Кт колонки E и F, "
            "остатки на конец по Дт/Кт колонки G и H"})
        assert response.status_code == 200, response.text
        assert client.get(f"/api/transformations/{tid}").json()["data"]["status"] == "COMPLETED"


def login(client, email):
    assert client.post("/api/auth/login", json={"email": email, "password": "TestPassword123!"}).status_code == 200


def test_api_private_shared_import_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "api.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    initialize_database()
    for email, role in [("a@test.local", "USER"), ("b@test.local", "USER"), ("admin@test.local", "ADMIN")]:
        add_user(email, "TestPassword123!", role)
    _, card, store = saved_card(tmp_path)
    package = store.export("owner").model_dump_json()
    # Start with an empty destination, as on a new installation.
    monkeypatch.setattr(settings, "document_memory_path", tmp_path / "imported")
    with TestClient(app) as first, TestClient(app) as second, TestClient(app) as admin:
        assert first.get("/api/document-memory").status_code == 401
        login(first, "a@test.local")
        login(second, "b@test.local")
        login(admin, "admin@test.local")
        assert first.post("/api/document-memory/import", files={"file": ("memory.json", package)}).status_code == 200
        assert second.get("/api/document-memory").json()["data"] == []
        endpoint = f"/api/document-memory/{card.id}/status"
        payload = {"revision": 1, "status": "approved", "scope": "private"}
        assert second.post(endpoint, json=payload).status_code == 404
        assert first.post(endpoint, json={**payload, "scope": "shared"}).status_code == 403
        assert first.post(endpoint, json=payload).status_code == 200
        assert admin.post(endpoint, json={**payload, "scope": "shared"}).status_code == 200
        assert len(second.get("/api/document-memory").json()["data"]) == 1
        assert first.post(endpoint, json={**payload, "status": "disabled"}).status_code == 403
        exported = first.get("/api/document-memory/export")
        assert exported.status_code == 200
        assert "owner_id" not in exported.text
        invalid = json.loads(package)
        invalid["format_version"] = 99
        assert first.post("/api/document-memory/import", files={"file": ("bad.json", json.dumps(invalid))}).status_code == 422
        assert first.post("/api/document-memory/import", files={"file": ("large.json", b"x" * (20 * 1024 * 1024 + 1))}).status_code == 413


def test_pipeline_learns_from_recovery_then_reuses_without_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "pipeline.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    initialize_database()
    add_user("a@test.local", "TestPassword123!", "USER")
    path = tmp_path / "source.xlsx"
    recovery_workbook(path)
    planner, calls = fake_planner(monkeypatch, answer_layout())
    monkeypatch.setattr("backend.app.services.pipeline.LLMPlanner", lambda: planner)
    with TestClient(app) as client:
        login(client, "a@test.local")
        def upload():
            response = client.post("/api/transformations", files={"file": ("osv.xlsx", BytesIO(path.read_bytes()))},
                data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
            assert response.status_code == 200, response.text
            tid = response.json()["data"]["id"]
            detail = client.get(f"/api/transformations/{tid}").json()["data"]
            assert detail["status"] == "COMPLETED", detail
            return tid, detail
        first, _ = upload()
        assert calls == ["first"]
        records = client.get("/api/document-memory").json()["data"]
        assert len(records) == 1
        card = records[0]["card"]
        assert records[0]["status"] == "draft"
        assert card["verified_rows"] == 3
        client.post(f"/api/document-memory/{card['id']}/status",
                    json={"revision": 1, "status": "approved", "scope": "private"})
        def forbidden(*a, **kw):
            pytest.fail("Подтверждённая память должна обойти LLM")
        monkeypatch.setattr(planner, "recognize_structure", forbidden)
        monkeypatch.setattr(planner, "choose_candidate", forbidden)
        second, detail = upload()
        assert detail["llm_used"] is False
        assert detail["processed_rows"] == 3
        events = client.get(f"/api/transformations/{second}/memo").json()["data"]["events"]
        assert any(e["event_type"] == "MEMORY_APPLIED" for e in events)
        assert calls == ["first"]
        assert client.post(f"/api/document-memory/from-transformation/{first}").status_code == 200
        assert len(client.get("/api/document-memory").json()["data"]) == 1
        profiles = client.get("/api/transformations/document-profiles").json()["data"]
        assert len(profiles) == 5
        assert sum(p["available"] for p in profiles) == 2
