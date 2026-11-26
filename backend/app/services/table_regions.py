"""Conservative detection of the data region and a separate trailing control."""
from dataclasses import dataclass
import re

from backend.app.services.account_structure import ACCOUNT_CODE, MONEY_KEYS


SERVICE_MARKER = re.compile(
    r"(ответственн|подпис|должност|расшифровк|руководител|главн.{0,8}бухгалтер|м\.?\s*п\.?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TrailingRegion:
    data_end: int
    control_rows: tuple[int, ...] = ()
    service_rows: tuple[int, ...] = ()
    confident: bool = False


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

def trailing_region(sheet, mapping, start, end):
    """Separate a conservative signature footer and a preceding totals row."""
    if end < start or not all(mapping.get(key) for key in ("account", "account_name", *MONEY_KEYS)):
        return TrailingRegion(end)
    money_columns = {mapping[key] for key in MONEY_KEYS}

    def row_nonempty(row):
        return any(not is_blank(cell.value) for cell in sheet[row])

    def has_money_number(row):
        return any(
            isinstance(sheet.cell(row, column).value, (int, float))
            and not isinstance(sheet.cell(row, column).value, bool)
            for column in money_columns
        )

    def is_service_marker(row):
        text = " ".join(str(cell.value).strip() for cell in sheet[row] if not is_blank(cell.value))
        account = str(sheet.cell(row, mapping["account"]).value or "").strip()
        return bool(SERVICE_MARKER.search(text)) and not ACCOUNT_CODE.fullmatch(account) and not has_money_number(row)

    service_start = next(
        (row for row in range(max(start, end - 24), end + 1) if is_service_marker(row)),
        None,
    )
    service_rows = ()
    boundary = end
    if service_start is not None:
        trailing_nonempty = [row for row in range(service_start, end + 1) if row_nonempty(row)]
        safe_footer = all(
            not has_money_number(row)
            and not ACCOUNT_CODE.fullmatch(str(sheet.cell(row, mapping["account"]).value or "").strip())
            for row in trailing_nonempty
        )
        if safe_footer:
            service_rows = tuple(trailing_nonempty)
            boundary = next((row for row in range(service_start - 1, start - 1, -1) if row_nonempty(row)), start - 1)

    if boundary < start:
        return TrailingRegion(end)
    control = trailing_control(sheet, mapping, start, boundary)
    data_end = control - 1 if control else boundary
    last_data_has_numbers = data_end >= start and has_money_number(data_end)
    confident = bool(service_rows or control or (boundary == end and last_data_has_numbers))
    return TrailingRegion(
        data_end=data_end,
        control_rows=(control,) if control else (),
        service_rows=service_rows,
        confident=confident,
    )


__all__ = ["TrailingRegion", "is_blank", "trailing_control", "trailing_region"]
