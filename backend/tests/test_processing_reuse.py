from copy import deepcopy
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from backend.app.models.domain import DataType
from backend.app.services.checked_result import CheckedResult, reuse_or_parse
from backend.app.services.hypothesis_planner import SourceStructure, choose_plan
from backend.app.services.parser import parse_workbook
from backend.app.services.sheet_cache import SheetReadCache
from backend.tests.test_name_column_quality import make_book
from backend.tests.test_planner_trace import baseline_spec


def analytic_book(path):
    book = Workbook()
    sheet = book.active
    sheet.append(["Счет", "Наименование счета", "Наименование счета",
                  "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт",
                  "Сальдо на конец Дт", "Сальдо на конец Кт"])
    sheet.append(["51", "Расчетные счета", None, 100, 0, 50, 20, 130, 0])
    for i in range(25):
        sheet.append([None, None, f"Детализация {i}", 4, 0, 2, 0.8, 5.2, 0])
    sheet.append(["80", "Уставный капитал", None, 0, 100, 20, 50, 0, 130])
    for i in range(25):
        sheet.append([None, None, f"Детализация {i}", 0, 4, 0.8, 2, 0, 5.2])
    sheet.append([None, "Итого", None, 100, 100, 70, 70, 130, 130])
    book.save(path)
    book.close()


def test_analytic_rows_do_not_penalize_account_names(tmp_path):
    path = tmp_path / "analytic.xlsx"
    analytic_book(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    assert selection.confident
    assert selection.best.spec.column_mapping["account_name"] == 2
    quality = selection.best.evidence["account_name_quality"]
    assert quality["checked"] == 2
    assert quality["nonempty"] == 2
    assert quality["penalty"] == 0
    assert len(selection.best.parsed.rows) == 52
    assert all(row["account"] for row in selection.best.parsed.rows)
    wrong = [candidate for candidate in selection.candidates if candidate.spec.column_mapping["account_name"] == 3]
    assert wrong
    assert all(candidate.evidence["account_name_quality"]["textual"] == 0 for candidate in wrong)


def test_sheet_dimensions_are_not_recomputed_per_row(monkeypatch):
    book = Workbook()
    sheet = book.active
    for row in range(100):
        sheet.append([row, "text", 0])
    original = Worksheet.max_column.fget
    calls = []
    monkeypatch.setattr(Worksheet, "max_column", property(lambda sheet: (calls.append(1), original(sheet))[1]))
    cache = SheetReadCache(sheet)
    for row in range(1, 101):
        first = cache.row(row)
        assert cache.row(row) is first
    assert len(calls) == 1
    book.close()


def test_cached_and_uncached_parsing_are_identical(tmp_path, monkeypatch):
    path = tmp_path / "source.xlsx"
    make_book(path)
    selection = choose_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    source = SourceStructure(path)
    try:
        spec = selection.best.spec
        for name_column in [2, 3, None]:
            variant = spec.model_copy(deep=True)
            variant.column_mapping["account_name"] = name_column
            evaluated = source.evaluate(variant, DataType.TRIAL_BALANCE)
            plain = parse_workbook(path, variant, DataType.TRIAL_BALANCE)
            assert evaluated.parsed == plain
        assert len(source.sheet_caches) == 1
        assert len(source.formula_merges) == 1
        assert len(source.header_caches) == 1
    finally:
        source.close()


def test_result_reuse_requires_exact_spec_and_does_not_mutate_original():
    spec = baseline_spec()
    parsed = SimpleNamespace(rows=[{"account": "01"}])
    validation = {"warnings": ["test"]}
    checked = CheckedResult(spec, parsed, validation)
    profile = SimpleNamespace(parse=lambda *args: pytest.fail("Повторный разбор"),
                              validate=lambda *args: pytest.fail("Повторная проверка"))
    reused, validated, hit = reuse_or_parse(profile, "unused", spec.model_copy(deep=True), [checked])
    assert hit
    reused.rows[0]["account"] = "99"
    validated["warnings"].append("new")
    assert parsed.rows[0]["account"] == "01"
    assert validation["warnings"] == ["test"]
    for change in [lambda s: s.column_mapping.update(account=2),
                   lambda s: setattr(s, "data_end_row", 10),
                   lambda s: s.row_rules.skip_keywords.append("other"),
                   lambda s: s.notes.append("new")]:
        changed = spec.model_copy(deep=True)
        change(changed)
        assert checked.get(changed) is None
    # In-place modification of the original spec must also invalidate the snapshot.
    spec.data_end_row += 1
    assert checked.get(spec) is None
    calls = []
    profile = SimpleNamespace(parse=lambda *args: calls.append("parse") or parsed,
                              validate=lambda *args: calls.append("validate") or validation)
    assert reuse_or_parse(profile, "unused", spec, [checked])[2] is False
    assert calls == ["parse", "validate"]
