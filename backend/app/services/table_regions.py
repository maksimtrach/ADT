"""Conservative detection of the data region and a separate trailing control."""
from backend.app.services.account_structure import ACCOUNT_CODE, MONEY_KEYS


def is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def trailing_control(sheet, mapping, start, end):
    if end < start + 2 or not all(mapping.get(k) for k in (*MONEY_KEYS, "account", "account_name")):
        return None
    if any(not is_blank(sheet.cell(end, mapping[k]).value) for k in ("account", "account_name")):
        return None
    money = {mapping[k] for k in MONEY_KEYS}
    if any(not is_blank(c.value) for c in sheet[end] if c.column not in money):
        return None
    if not all(isinstance(sheet.cell(end, c).value, (int, float)) and not isinstance(sheet.cell(end, c).value, bool) for c in money):
        return None
    # A trailing unnamed currency line immediately below EUR/USD is not a total.
    previous = end - 1
    while previous >= start and all(is_blank(c.value) for c in sheet[previous]):
        previous -= 1
    code = str(sheet.cell(previous, mapping["account"]).value or "").strip() if previous >= start else ""
    name = sheet.cell(previous, mapping["account_name"]).value if previous >= start else None
    return end if ACCOUNT_CODE.fullmatch(code) and not is_blank(name) else None
