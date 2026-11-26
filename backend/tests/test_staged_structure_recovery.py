import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.models.domain import DataType
from backend.app.services.hypothesis_planner import choose_plan
from backend.app.services.llm.base import LLMCallResult
from backend.app.services.planner import LLMPlanner


def footer_workbook(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "ОСВ"
    sheet.append([
        "Счет", "Наименование", "Сальдо на начало Дт", "Сальдо на начало Кт",
        "Оборот Дт", "Оборот Кт", "Сальдо на конец Дт", "Сальдо на конец Кт",
    ])
    sheet.append(["01", "Основные средства", 100, 0, 20, 10, 110, 0])
    sheet.append(["01.01", "Объекты", 100, 0, 20, 10, 110, 0])
    sheet.append([None, None, 100, 0, 20, 10, 110, 0])
    sheet.append([])
    sheet.append(["Сформировано системой"])
    book.save(path)
    book.close()


def test_footer_is_a_separate_llm_stage_with_fixed_header(tmp_path, monkeypatch):
    path = tmp_path / "footer.xlsx"
    footer_workbook(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")

    config = LLMConfiguration(
        active_profile="test",
        profiles={"test": LLMProfile(protocol="openai", base_url="http://unused.test/v1", model="test")},
    )
    calls = []

    def complete(adapter, messages, schema, schema_name, trace=None):
        calls.append(schema_name)
        assert schema_name == "FooterAnswer"
        request = json.loads(messages[1]["content"])
        assert request["fixed_header"]["column_mapping"]["account"] == 1
        assert request["tail"]["physical_last_row"] == 6
        assert any(row["row"] == 6 for row in request["tail"]["rows"])
        answer = {
            "layout": {"last_data_row": 3, "control_rows": [4], "service_rows": [6]},
            "explanation": "Строка 4 — итог, строка 6 — служебный футер",
            "clarification": None,
        }
        return LLMCallResult(json.dumps(answer, ensure_ascii=False), {}, 1)

    monkeypatch.setattr("backend.app.services.llm.base.LLMAdapter.complete", complete)
    events = []
    result = LLMPlanner(config).recognize_structure(
        path,
        DataType.TRIAL_BALANCE,
        "trial_balance_flat_v1",
        selection,
        trace=lambda event, details: events.append(event),
    )

    assert calls == ["FooterAnswer"]
    assert result.eligible
    assert result.llm_used is True
    assert result.spec.data_end_row == 3
    assert result.spec.control_rows == [4]
    assert "LLM_HEADER_SKIPPED" in events
    assert "LLM_FOOTER_RECOGNITION_STARTED" in events
    assert "LLM_FOOTER_ACCEPTED" in events
    assert events[-1] == "LLM_STRUCTURE_ACCEPTED"


def test_signature_footer_is_removed_before_llm_for_last_problem_file():
    path = Path("test-data/complex/ОСВ_К3_2024.xlsx")
    if not path.exists():
        pytest.skip("Локальный проблемный файл отсутствует")
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    assert selection.best.eligible
    assert selection.best.spec.data_end_row == 73
    assert selection.best.spec.control_rows == [74]
    assert not selection.best.parsed.numeric_errors
    assert selection.best.validation["status"] == "PASSED"
