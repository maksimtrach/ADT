from __future__ import annotations

import json

import pytest

from backend.app.models.domain import DataType
from backend.app.services.file_profiler import profile_workbook, validate_xlsx
from backend.app.services.parser import parse_workbook
from backend.app.services.planner import rule_plan
from backend.app.services.validator import validate_result
from scripts.generate_test_files import EXPECTED, OUTPUT, generate_all


@pytest.fixture(scope="session", autouse=True)
def generated_files():
    generate_all()


@pytest.mark.parametrize(
    ("filename", "data_type", "template_id"),
    [
        ("01-simple-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("02-grouped-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("03-merged-trial-balance", DataType.TRIAL_BALANCE, "trial_balance_flat_v1"),
        ("04-unusual-journal", DataType.JOURNAL, "journal_flat_v1"),
    ],
)
def test_synthetic_file_matches_expected(filename: str, data_type: DataType, template_id: str):
    path = OUTPUT / f"{filename}.xlsx"
    validate_xlsx(path, 25 * 1024 * 1024)
    profile = profile_workbook(path)
    assert profile["sheet_count"] == 1
    assert profile["sheets"][0]["nonempty_rows"] >= 3
    spec = rule_plan(path, data_type, template_id)
    parsed = parse_workbook(path, spec, data_type)
    expected = json.loads((EXPECTED / f"{filename}.json").read_text(encoding="utf-8"))
    actual = [{key: value for key, value in row.items() if not key.startswith("_")} for row in parsed.rows]
    assert actual == expected
    validation = validate_result(parsed, spec)
    assert validation["status"] in {"PASSED", "WARNINGS"}
    assert not validation["missing_required"]

