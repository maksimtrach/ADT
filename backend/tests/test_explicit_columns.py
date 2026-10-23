from io import BytesIO
from pathlib import Path
from time import perf_counter
import hashlib

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import initialize_database
from backend.app.main import app
from backend.app.models.domain import DataType
from backend.app.services.account_structure import MONEY_KEYS
from backend.app.services.feedback_rules import explicit_column_feedback
from backend.app.services.explicit_layout import plan_from_explicit_mapping
from backend.app.services.hypothesis_planner import SourceStructure, choose_plan
from backend.app.services.parser import normalize_number
from backend.app.services.planner import LLMPlanner
from backend.app.services.structure_recovery import StructureLayout, build_recovered_spec
from backend.tests.test_api import add_user

FEEDBACK = "Бери номер счета из колонки A, наименование счета из колонки B, остатки по Дт/Кт колонки С и D, обороты Дт/Кт колонки E и F, остатки на конец по Дт/Кт колонки G и H"
MAPPING = {"account": 1, "account_name": 2, **dict(zip(MONEY_KEYS, range(3, 9)))}


@pytest.fixture
def missing_header_file(tmp_path):
    path = tmp_path / "missing-header.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append(["Отчет"])
    sheet.append([])
    sheet.append(["01", "Основные средства", 100, " ", 20, 10, 110, " "])
    sheet.append(["01.1", "Объекты", 100, " ", 20, 10, 110, " "])
    sheet.append(["02", "Амортизация", " ", 100, 10, 20, " ", 110])
    sheet.append(["02.1", "Объекты", " ", 100, 10, 20, " ", 110])
    sheet.append(["Фирма Тест", None, 10, " ", 2, 1, 11, " "])
    sheet.merge_cells("A7:B7")
    sheet.append([None, "евро", 10, " ", 2, 1, 11, " "])
    sheet.append([None, "евро в валюте", 1, " ", .2, .1, 1.1, " "])
    sheet.append([None, " в валюте", " ", " ", " ", " ", " ", " "])
    sheet.append(["002", "Хранение", 25, " ", " ", " ", 25, " "])
    sheet.append(["Н01", "Налоговый учет", 20, " ", " ", " ", 20, " "])
    sheet.append([None, None, 100, 100, 30, 30, 110, 110])
    book.save(path)
    book.close()
    return path


def test_complete_comment_is_a_locked_mapping():
    assert explicit_column_feedback(FEEDBACK) == MAPPING
    assert explicit_column_feedback("Брать счета из колонки А; наименование счета из колонки В") == {"account": 1, "account_name": 2}
    assert explicit_column_feedback("Брать счета из колонки A, а строки 5–8 исправить") is None
    with pytest.raises(AppError, match="Противоречивые"):
        explicit_column_feedback("Брать счета из колонки A, брать счета из колонки B")
    with pytest.raises(AppError, match="разным полям"):
        explicit_column_feedback("остатки по Дт/Кт колонки C и C")


@pytest.mark.parametrize("value", [None, "", " ", "\t\n", "\u00a0", "\u202f"])
def test_whitespace_is_missing_not_zero(value):
    assert normalize_number(value) == (None, False)


def test_explicit_layout_preserves_analytics_and_separates_total(missing_header_file):
    result = plan_from_explicit_mapping(missing_header_file, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", MAPPING).best
    assert result.eligible
    assert result.spec.data_end_row == 12
    assert result.spec.control_rows == [13]
    assert len(result.parsed.rows) == 10
    assert not result.parsed.numeric_errors
    rows = {r["_source_row"]: r for r in result.parsed.rows}
    assert rows[11]["account"] == "002"
    assert rows[12]["account"] == "Н01"
    assert rows[3]["opening_credit"] is None
    assert rows[8]["counterparty"] == "Фирма Тест"
    assert rows[9]["parent_analytic_row"] == "7"
    assert rows[9]["currency"] == "EUR"
    assert rows[9]["amount_basis"] == "В валюте EUR"
    assert rows[10]["_row_kind"] == "service"
    assert result.validation["aggregation"] == "two_digit_root_accounts"
    assert result.validation["control_row_count"] == 2
    assert all(c["passed"] for c in result.validation["control_comparisons"].values())


@pytest.mark.parametrize("data_end", [12, 13])
def test_llm_flat_with_name_is_normalized_and_total_retained(missing_header_file, data_end):
    source = SourceStructure(missing_header_file)
    try:
        layout = StructureLayout(sheet_name="ОСВ", header_start_row=1, header_end_row=2,
                                 data_start_row=3, data_end_row=data_end, row_mode="flat",
                                 column_mapping={**MAPPING, "analytics": None, "subaccount": None})
        spec = build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
        assert spec.row_rules.account_tree
        assert spec.control_rows == [13]
        assert source.evaluate(spec, DataType.TRIAL_BALANCE, region_start=1).eligible
        layout.data_end_row = 11
        with pytest.raises(AppError, match="обрезает данные"):
            build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
        layout.data_end_row = data_end
        layout.column_mapping["opening_debit"] = 999
        spec = build_recovered_spec(layout, source, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", MAPPING)
        assert spec.column_mapping["opening_debit"] == 3
    finally:
        source.close()


def test_bad_total_is_not_discarded_or_forced_to_match(missing_header_file):
    book = load_workbook(missing_header_file)
    book.active["C13"] = 999
    book.save(missing_header_file)
    book.close()
    result = plan_from_explicit_mapping(missing_header_file, DataType.TRIAL_BALANCE, "trial_balance_flat_v1", MAPPING).best
    assert not result.eligible
    assert result.validation["status"] == "FAILED"
    assert result.validation["control_rows"] == [13]
    assert result.validation["control_comparisons"]["opening_debit"]["passed"] is False


def test_real_comment_retry_succeeds_without_model(tmp_path, monkeypatch):
    path = Path("test-data/complex/Общая ОСВ_тест_4.xlsx")
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.sqlite3")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "storage")
    calls = []

    def initial_failure(*args, **kwargs):
        calls.append("recovery")
        raise AppError(422, "Требуется указание колонок", "STRUCTURE_AMBIGUOUS")

    monkeypatch.setattr(LLMPlanner, "recognize_structure", initial_failure)
    for method in ("available", "choose_candidate", "refine"):
        monkeypatch.setattr(LLMPlanner, method, lambda *a, **kw: pytest.fail("Модель для явного комментария не нужна"))
    initialize_database()
    add_user("test@example.test", "TestPassword123!", "USER")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": "test@example.test", "password": "TestPassword123!"})
        with path.open("rb") as source:
            response = client.post("/api/transformations", files={"file": (path.name, source)},
                                   data={"data_type": "TRIAL_BALANCE", "template_id": "trial_balance_flat_v1"})
        tid = response.json()["data"]["id"]
        assert client.get(f"/api/transformations/{tid}").json()["data"]["status"] == "FAILED"
        started = perf_counter()
        retried = client.post(f"/api/transformations/{tid}/retry", json={"feedback": FEEDBACK})
        assert retried.status_code == 200
        detail = client.get(f"/api/transformations/{tid}").json()["data"]
        assert detail["status"] == "COMPLETED", detail
        assert detail["processed_rows"] == 194
        assert detail["unprocessed_rows"] == 0
        assert detail["llm_used"] is False
        assert detail["controls"]["control_rows"] == [202]
        assert all(c["passed"] for c in detail["controls"]["control_comparisons"].values())
        assert calls == ["recovery"]
        print(f"Full comment retry: {perf_counter() - started:.3f}s")
        memo = client.get(f"/api/transformations/{tid}/memo").json()["data"]
        assert memo["run"]["feedback"] == FEEDBACK
        assert any(e["event_type"] == "USER_MAPPING_APPLIED" and e["details"]["mapping"] == MAPPING for e in memo["events"])
        assert not any(e["event_type"].startswith("LLM_") for e in memo["events"])
        download = client.get(f"/api/transformations/{tid}/download")
        book = load_workbook(BytesIO(download.content), data_only=True)
        rows = list(book["Результат"].values)
        assert len(rows) == 195
        account = rows[0].index("Счет")
        assert {"002", "007", "010", "Н01"}.issubset({row[account] for row in rows[1:]})
        book.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("filename,expected", [("Общая ОСВ_тест_1.xlsx",129), ("Общая ОСВ_тест_2.xlsx",132), ("Общая ОСВ_тест_3.xlsx",233)])
def test_renamed_previous_workbooks_still_parse(filename, expected):
    path = Path("test-data/complex") / filename
    if not path.exists():
        pytest.skip("Локальный пользовательский файл отсутствует")
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    assert len(selection.best.parsed.rows) == expected
