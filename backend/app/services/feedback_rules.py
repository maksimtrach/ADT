"""Recognize complete explicit column instructions, never partial free text."""
import re
from openpyxl.utils import column_index_from_string
from backend.app.core.errors import AppError


def _column(value):
    value = value.upper().translate(str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY"))
    try:
        result = int(value) if value.isdigit() else column_index_from_string(value)
        return result if 1 <= result <= 16384 else None
    except ValueError:
        return None


def explicit_column_feedback(feedback: str | None) -> dict[str, int] | None:
    if not feedback or not feedback.strip():
        return None
    clauses = re.split(r"[,;\n]+", feedback.lower().replace("ё", "е").strip().rstrip(".!"))
    mapping = {}
    for clause in clauses:
        clause = re.sub(r"^\s*(?:загрузи|бери|возьми|используй|загрузить|брать)\s+", "", clause).strip()
        single = re.fullmatch(
            r"(?P<field>(?:номера?\s+)?счет(?:а|ов)?|наименование\s+счета)"
            r"\s+(?:из\s+)?(?:колонки|колонка|столбца|столбец)\s+(?P<column>[a-zа-я]{1,3}|[0-9]{1,5})", clause)
        pair = re.fullmatch(
            r"(?P<field>остатки|остаток|сальдо|обороты|оборот)"
            r"(?:\s+на\s+(?P<period>начало|конец)(?:\s+периода)?)?"
            r"\s+(?:по\s+)?дт\s*/\s*кт\s+(?:из\s+)?(?:колонки|колонок|столбцы|столбцов)\s+"
            r"(?P<debit>[a-zа-я]{1,3}|[0-9]{1,5})\s*(?:и|/|–|-)\s*(?P<credit>[a-zа-я]{1,3}|[0-9]{1,5})", clause)
        if single:
            updates = {"account_name" if single["field"].startswith("наименование") else "account": _column(single["column"])}
        elif pair:
            prefix = "turnover" if pair["field"].startswith("оборот") else "closing" if pair["period"] == "конец" else "opening"
            if prefix == "turnover" and pair["period"]:
                return None
            updates = {prefix + "_debit": _column(pair["debit"]), prefix + "_credit": _column(pair["credit"])}
        else:
            return None  # Do not silently discard the unrecognized part of a comment.
        if any(v is None for v in updates.values()):
            raise AppError(422, "Недопустимая колонка в комментарии", "INVALID_COLUMN_MAPPING")
        if any(k in mapping and mapping[k] != v for k, v in updates.items()):
            raise AppError(422, "Противоречивые указания колонок для одного поля", "INVALID_COLUMN_MAPPING")
        mapping.update(updates)
    if len(set(mapping.values())) != len(mapping):
        raise AppError(422, "Одна колонка назначена разным полям", "INVALID_COLUMN_MAPPING")
    return mapping
