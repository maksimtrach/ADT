from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "test-data" / "generated"
EXPECTED = ROOT / "test-data" / "expected"


def style_header(worksheet) -> None:
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="001529")


def save_expected(name: str, rows: list[dict]) -> None:
    EXPECTED.mkdir(parents=True, exist_ok=True)
    (EXPECTED / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def simple_trial_balance() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ОСВ"
    sheet.append(["Счет", "Субсчет", "Контрагент", "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт", "Сальдо на конец Дт", "Сальдо на конец Кт"])
    rows = [
        ["50.01", None, "Касса", 1000, None, 500, 300, 1200, None],
        ["51", "51.01", "Основной счет", 15000, None, 2500, 4000, 13500, None],
        ["60.01", None, "Поставщик Альфа", None, 2000, 7500, 6000, None, 500],
    ]
    for row in rows:
        sheet.append(row)
    sheet.append(["Итого", None, None, 16000, 2000, 10500, 10300, 14700, 500])
    style_header(sheet)
    workbook.save(OUTPUT / "01-simple-trial-balance.xlsx")
    save_expected("01-simple-trial-balance", [
        {"account": "50.01", "subaccount": None, "analytics": "Касса", "opening_debit": 1000.0, "opening_credit": None, "turnover_debit": 500.0, "turnover_credit": 300.0, "closing_debit": 1200.0, "closing_credit": None},
        {"account": "51", "subaccount": "51.01", "analytics": "Основной счет", "opening_debit": 15000.0, "opening_credit": None, "turnover_debit": 2500.0, "turnover_credit": 4000.0, "closing_debit": 13500.0, "closing_credit": None},
        {"account": "60.01", "subaccount": None, "analytics": "Поставщик Альфа", "opening_debit": None, "opening_credit": 2000.0, "turnover_debit": 7500.0, "turnover_credit": 6000.0, "closing_debit": None, "closing_credit": 500.0},
    ])


def grouped_trial_balance() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ведомость"
    sheet.append(["Счет", "Субсчет", "Аналитика", "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт", "Сальдо на конец Дт", "Сальдо на конец Кт"])
    sheet.append(["62", "62.01", None, None, None, None, None, None, None])
    sheet.append([None, None, "Покупатель Север", 2000, None, 3000, 1000, 4000, None])
    sheet.append([None, None, "Покупатель Юг", 500, None, 700, 300, 900, None])
    sheet.append(["Итого по 62.01", None, None, 2500, None, 3700, 1300, 4900, None])
    style_header(sheet)
    workbook.save(OUTPUT / "02-grouped-trial-balance.xlsx")
    save_expected("02-grouped-trial-balance", [
        {"account": "62", "subaccount": "62.01", "analytics": "Покупатель Север", "opening_debit": 2000.0, "opening_credit": None, "turnover_debit": 3000.0, "turnover_credit": 1000.0, "closing_debit": 4000.0, "closing_credit": None},
        {"account": "62", "subaccount": "62.01", "analytics": "Покупатель Юг", "opening_debit": 500.0, "opening_credit": None, "turnover_debit": 700.0, "turnover_credit": 300.0, "closing_debit": 900.0, "closing_credit": None},
    ])


def merged_trial_balance() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ОСВ с итогами"
    sheet.append(["Счет", "Субсчет", "Контрагент", "Сальдо на начало Дт", "Сальдо на начало Кт", "Оборот Дт", "Оборот Кт", "Сальдо на конец Дт", "Сальдо на конец Кт"])
    sheet.append(["76", "76.05", "Контрагент Один", 100, None, 400, 250, 250, None])
    sheet.append([None, None, "Контрагент Два", 200, None, 300, 100, 400, None])
    sheet.merge_cells("A2:A3")
    sheet.merge_cells("B2:B3")
    sheet.append(["Итого по счету 76", None, None, 300, None, 700, 350, 650, None])
    style_header(sheet)
    workbook.save(OUTPUT / "03-merged-trial-balance.xlsx")
    save_expected("03-merged-trial-balance", [
        {"account": "76", "subaccount": "76.05", "analytics": "Контрагент Один", "opening_debit": 100.0, "opening_credit": None, "turnover_debit": 400.0, "turnover_credit": 250.0, "closing_debit": 250.0, "closing_credit": None},
        {"account": "76", "subaccount": "76.05", "analytics": "Контрагент Два", "opening_debit": 200.0, "opening_credit": None, "turnover_debit": 300.0, "turnover_credit": 100.0, "closing_debit": 400.0, "closing_credit": None},
    ])


def unusual_journal() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Операции"
    sheet.append(["Период", "Документ №", "Дебетовый счет", "Кредитовый счет", "Субконто Дт", "Субконто Кт", "Сумма операции", "Описание"])
    sheet.append(["15.01.2026", "РН-001", "26", "60.01", "Основное производство", "Поставщик Альфа", "12 500,50", "Поступление услуг"])
    sheet.append(["16.01.2026", "ПП-002", "60.01", "51.01", "Поставщик Альфа", "Основной счет", 12500.50, "Оплата поставщику"])
    style_header(sheet)
    workbook.save(OUTPUT / "04-unusual-journal.xlsx")
    save_expected("04-unusual-journal", [
        {"date": "2026-01-15", "document_number": "РН-001", "debit_account": "26", "credit_account": "60.01", "debit_analytics": "Основное производство", "credit_analytics": "Поставщик Альфа", "amount": 12500.5, "description": "Поступление услуг"},
        {"date": "2026-01-16", "document_number": "ПП-002", "debit_account": "60.01", "credit_account": "51.01", "debit_analytics": "Поставщик Альфа", "credit_analytics": "Основной счет", "amount": 12500.5, "description": "Оплата поставщику"},
    ])


def generate_all() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    simple_trial_balance()
    grouped_trial_balance()
    merged_trial_balance()
    unusual_journal()
    print(f"Созданы синтетические файлы: {OUTPUT}")


if __name__ == "__main__":
    generate_all()

