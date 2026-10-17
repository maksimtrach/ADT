from backend.app.models.domain import DataType, ValueOverride
from backend.app.services.parser import parse_workbook
from backend.app.services.planner import rule_plan
from scripts.generate_test_files import OUTPUT, generate_all


def test_row_range_value_override_is_applied():
    generate_all()
    path = OUTPUT / "01-simple-trial-balance.xlsx"
    spec = rule_plan(path, DataType.TRIAL_BALANCE, "trial_balance_flat_v1")
    spec.value_overrides = [
        ValueOverride(
            target_field="account",
            from_row=4,
            to_row=4,
            value="62.02",
            reason="Замечание пользователя",
        )
    ]
    parsed = parse_workbook(path, spec, DataType.TRIAL_BALANCE)
    assert parsed.rows[2]["account"] == "62.02"
    assert parsed.rows[2]["_source_refs"]["account"].startswith("Правило пользователя")

