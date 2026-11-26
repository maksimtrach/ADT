import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import AppError
from backend.app.models.domain import DataType
from backend.app.services.explicit_feedback import parse_feedback
from backend.app.services.explicit_layout import plan_from_explicit_mapping
from backend.app.services.planner import LLMPlanner
from backend.app.main import app
from backend.tests.test_explicit_columns import missing_header_file, MAPPING
from backend.tests.test_reuse_and_llm_errors_api import prepare, upload


TEXT = """Счет: A
Наименование счета: B
Сальдо на начало Дт: C
Сальдо на начало Кт: D
Оборот Дт: E
Оборот Кт: F
Сальдо на конец Дт: G
Сальдо на конец Кт: H
Шапка: отсутствует
Данные: 3–12
Итоги: 13"""


@pytest.mark.parametrize("separator", [": ", " - колонка ", " — "])
def test_new_column_formats(separator):
    text = TEXT.split('Шапка:')[0].replace(': ', separator)
    assert parse_feedback(text).mapping == MAPPING


def test_numeric_columns_and_legacy_pairs():
    assert parse_feedback('Счет: 1; Наименование счета: 2').mapping == {'account': 1, 'account_name': 2}
    assert parse_feedback('Сальдо на начало Дт/Кт из колонок C-D').mapping == {'opening_debit': 3, 'opening_credit': 4}
    assert parse_feedback('Данные в строках 8-201\nИтоги 202').data == (8, 201)
    assert parse_feedback('Итоги: 202, 203').totals == [202, 203]


@pytest.mark.parametrize("text", [
    'Данные: 12–3', 'Данные: 0–12', 'Шапка:', 'Счет: A\nчто-то неизвестное',
    'Счет: A\nСчет: B', 'Счет: A\nНаименование счета: A', 'Итоги: 13,13',
    'Данные: 3–12\nДанные: 4–12', 'Шапка: нет\nШапка: 1–2', 'Итоги: 13\nИтоги: 14',
])
def test_bad_structured_comment_is_not_sent_to_llm(text):
    with pytest.raises(AppError):
        parse_feedback(text)


def test_free_prose_is_still_supported():
    assert not parse_feedback('Уточни структуру таблицы и обработай отрицательные суммы').mapping


def test_explicit_ranges_no_header(missing_header_file):
    instruction = parse_feedback(TEXT)
    plan = plan_from_explicit_mapping(missing_header_file, DataType.TRIAL_BALANCE,
                                     'trial_balance_flat_v1', instruction.mapping, instruction).best
    assert plan.eligible
    assert plan.spec.header_start_row is None
    assert plan.spec.header_end_row is None
    assert (plan.spec.data_start_row, plan.spec.data_end_row) == (3, 12)
    assert plan.spec.control_rows == [13]
    assert all(v['passed'] for v in plan.validation['control_comparisons'].values())


@pytest.mark.parametrize('replacement', [
    ('Данные: 3–12', 'Данные: 3–99'), ('Итоги: 13', 'Итоги: 12'),
    ('Шапка: отсутствует', 'Шапка: 1–3'), ('Итоги: 13', 'Итоги: 14'),
])
def test_conflicting_ranges_are_rejected(missing_header_file, replacement):
    instruction = parse_feedback(TEXT.replace(*replacement))
    with pytest.raises(AppError, match='пересека|границ|пределах'):
        plan_from_explicit_mapping(missing_header_file, DataType.TRIAL_BALANCE,
                                  'trial_balance_flat_v1', instruction.mapping, instruction)


def test_complete_feedback_retry_without_llm(tmp_path, monkeypatch):
    path = prepare(tmp_path, monkeypatch)
    # The fixture has two data rows, B:C merged only in its header.
    text = TEXT.replace('3–12', '2–3').replace('Итоги: 13', 'Итоги: нет')
    for method in ('choose_candidate', 'recognize_structure', 'refine'):
        monkeypatch.setattr(LLMPlanner, method, lambda *a, **kw: pytest.fail('LLM must not run'))
    with TestClient(app) as client:
        tid = upload(client, path)
        # This workbook has an extra blank name column, so shift money columns.
        text = text.replace('Дт: C', 'Дт: D').replace('Кт: D', 'Кт: E')
        text = text.replace('Оборот Дт: E', 'Оборот Дт: F').replace('Оборот Кт: F', 'Оборот Кт: G')
        text = text.replace('конец Дт: G', 'конец Дт: H').replace('конец Кт: H', 'конец Кт: I')
        response = client.post(f'/api/transformations/{tid}/retry', json={'feedback': text})
        assert response.status_code < 300, response.text
        detail = client.get(f'/api/transformations/{tid}').json()['data']
        assert detail['status'] == 'COMPLETED', detail
        assert not detail['llm_used']
        assert detail['recognized_structure']['data_start_row'] == 2
        assert detail['recognized_structure']['data_end_row'] == 3
