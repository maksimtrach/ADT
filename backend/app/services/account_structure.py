"""Conservative account/currency classification; never combine unknown units."""
from collections import defaultdict
import re

from backend.app.models.domain import TargetColumn

MONEY_KEYS = ("opening_debit", "opening_credit", "turnover_debit", "turnover_credit", "closing_debit", "closing_credit")
ACCOUNT_CODE = re.compile(r"^(?:\d{1,4}|[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]{0,11})(?:\.[0-9A-Za-zА-Яа-яЁё]+)*$")
CURRENCIES = {"USD", "EUR", "CNY", "JPY", "KRW", "RUB", "GBP", "CHF", "HKD", "TRY", "KZT", "BYN", "AED", "CAD", "AUD", "INR"}
DERIVED_ACCOUNT_FIELDS = {"parent_account", "account_level", "row_type", "source_code", "source_name", "currency", "amount_basis", "parent_source_row", "classification_note", "counterparty", "parent_analytic_row"}
SERVICE_COLUMNS = (
    TargetColumn(key="parent_account", label="Родительский счёт"),
    TargetColumn(key="account_level", label="Уровень счёта"),
    TargetColumn(key="row_type", label="Тип строки"),
    TargetColumn(key="parent_source_row", label="Строка родительского счёта"),
)


def result_columns(columns: list[TargetColumn]) -> list[TargetColumn]:
    """Append portable service metadata without changing the transformation target schema."""
    existing = {column.key for column in columns}
    return [*columns, *(column for column in SERVICE_COLUMNS if column.key not in existing)]


def currency_code(value):
    text = str(value or "").strip().upper()
    text = re.sub(r"\s+В ВАЛЮТЕ$", "", text)
    text = {"ЕВРО": "EUR", "ДОЛЛАР США": "USD", "ДОЛЛАРЫ США": "USD", "РУБЛИ": "RUB", "РУБЛЬ": "RUB"}.get(text, text)
    return "RUB" if text in {"РУБ", "РУБ.", "RUR"} else text if text in CURRENCIES else None


def account_columns(columns):
    return [
        TargetColumn(key="account", label="Счет", required=True),
        TargetColumn(key="account_name", label="Наименование счёта"),
        TargetColumn(key="parent_account", label="Родительский счёт"),
        TargetColumn(key="account_level", label="Уровень счёта"),
        TargetColumn(key="row_type", label="Тип строки"),
        TargetColumn(key="source_code", label="Код в исходной строке"),
        TargetColumn(key="source_name", label="Наименование в исходной строке"),
        TargetColumn(key="currency", label="Валюта аналитики"),
        TargetColumn(key="amount_basis", label="Тип суммы"),
        TargetColumn(key="parent_source_row", label="Строка родительского счёта"),
        TargetColumn(key="classification_note", label="Замечание классификации"),
        TargetColumn(key="counterparty", label="Контрагент"),
        TargetColumn(key="parent_analytic_row", label="Строка родительской аналитики"),
        *[column for column in columns if column.kind == "number"],
    ]


def annotate_account_rows(rows):
    accounts = []
    parent = None
    currency = None
    counterparty = None
    for row in rows:
        code = row.get("account")
        name = row.get("account_name")
        row.setdefault("source_code", code)
        row.setdefault("source_name", name)
        for source_key, key in (("source_code", "account"), ("source_name", "account_name")):
            if key in row["_source_refs"]:
                row["_source_refs"].setdefault(source_key, row["_source_refs"][key])
        row["_control_included"] = False
        row["classification_note"] = None
        is_account = bool(code and ACCOUNT_CODE.fullmatch(code) and (name or (code[0].isdigit() and not currency_code(code))))
        if is_account:
            row["_row_kind"] = "account"
            row["amount_basis"] = "Сумма строки счёта"
            accounts.append(row)
            parent, currency, counterparty = row, None, None
        else:
            label = str(name or code or "").strip()
            named_currency = currency_code(code) or currency_code(name)
            in_currency = bool(re.search(r"\bв валюте\b", label, re.I))
            party = bool(code and not ACCOUNT_CODE.fullmatch(code) and re.search(r"\b(ооо|оао|зао|чао|ао|фирма|srl|s\.r\.l|ltd)\b", code, re.I))
            if party:
                counterparty, currency = row, None
            elif named_currency:
                currency = named_currency
            elif code:
                currency, counterparty = None, None
            row["_row_kind"] = "counterparty" if party else "currency" if named_currency or in_currency else "unlabelled" if not code and not name else "unknown"
            if not any(row.get(k) is not None for k in MONEY_KEYS):
                row["_row_kind"] = "service"
            row["row_type"] = {"currency": "Валютная расшифровка", "unlabelled": "Неподписанная строка", "unknown": "Неопределённая аналитика", "counterparty": "Контрагент", "service": "Служебная строка без сумм"}[row["_row_kind"]]
            row["currency"] = currency
            row["counterparty"] = counterparty.get("source_code") if counterparty else None
            row["parent_analytic_row"] = str(counterparty["_source_row"]) if counterparty and counterparty is not row else None
            row["amount_basis"] = ("В валюте " + (currency or "(не указана)")) if in_currency else "Не определён — исключено из суммирования"
            row["classification_note"] = "Тип суммы указан в подписи; повторно в итог не включается" if in_currency else "Сохранена исходная строка; единица суммы не подтверждена"
            row["account"] = parent.get("account") if parent else None
            row["account_name"] = parent.get("account_name") if parent else None
            row["parent_source_row"] = str(parent["_source_row"]) if parent else None
            if parent:
                row["_source_refs"]["account"] = parent["_source_refs"].get("account", "")
                row["_source_refs"]["account_name"] = parent["_source_refs"].get("account_name", "")

    # Two numeric roots with different names may mean 01 and 001. Do not guess.
    names = defaultdict(set)
    for row in accounts:
        names[row["account"]].add(row.get("account_name"))
    ambiguous = {code for code, values in names.items() if "." not in code and len(values) > 1}
    prefixes = {row["account"].split(".")[0] for row in accounts if "." in row["account"]}
    for row in accounts:
        code = row["account"]
        matches = [p for p in prefixes if p.isdigit() and code.isdigit() and int(p) == int(code)]
        row["_ambiguous_account"] = code in ambiguous
        if code in ambiguous:
            row["classification_note"] = "Одинаковый исходный код у разных счетов; ведущие нули не восстановлены"
        elif "." not in code and len(matches) == 1 and len(matches[0]) > len(code):
            row["account"] = matches[0]

    by_code = defaultdict(list)
    for row in accounts:
        by_code[row["account"]].append(row)
    for row in accounts:
        code = row["account"]
        parts = code.split(".")
        parent_code = next((".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)
                            if len(by_code[".".join(parts[:i])]) == 1), None)
        row["parent_account"] = parent_code
        row["parent_source_row"] = str(by_code[parent_code][0]["_source_row"]) if parent_code else None
        row["account_level"] = str(len(parts))
        row["row_type"] = "Сводный счёт" if any(c.startswith(code + ".") for c in by_code) else "Детальный счёт"
        row["_control_included"] = parent_code is None
    by_row = {str(row["_source_row"]): row for row in accounts}
    for row in rows:
        if row["_row_kind"] != "account" and row.get("parent_source_row") in by_row:
            parent = by_row[row["parent_source_row"]]
            row["account"] = parent["account"]
            row["parent_account"] = parent["account"]
            row["account_level"] = parent["account_level"]
