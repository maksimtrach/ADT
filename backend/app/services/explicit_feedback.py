"""Structured user instructions; free prose remains an LLM request."""
from dataclasses import dataclass, field
import re

from backend.app.core.errors import AppError
from backend.app.services.feedback_rules import _column, legacy_column_feedback


@dataclass
class ExplicitFeedback:
    mapping: dict[str, int] = field(default_factory=dict)
    data: tuple[int, int] | None = None
    header: tuple[int, int] | None = None
    header_set: bool = False
    totals: list[int] | None = None

    @property
    def has_bounds(self):
        return self.data is not None or self.header_set or self.totals is not None


def invalid(message):
    raise AppError(422, message, "INVALID_EXPLICIT_LAYOUT")


def parse_feedback(text):
    result = ExplicitFeedback()
    if not text or not text.strip():
        return result
    unknown = []
    structured = False
    for raw in re.split(r"[;\n]+", text):
        # Commas remain compatible with the old column-only syntax.
        clauses = [raw] if re.match(r"\s*итог", raw, re.I) else raw.split(',')
        for clause in clauses:
            value = clause.lower().replace('ё', 'е').strip().rstrip('.!')
            if not value:
                continue
            bound = re.fullmatch(r"(шапка|данные|итоги|итоговые строки)\s*(?::|[-—–]|в строках)?\s*(.*)", value)
            if bound:
                structured = True
                key, body = bound.groups()
                if key.startswith('итог'):
                    if body in ('нет', 'отсутствуют'):
                        parsed = []
                    elif re.fullmatch(r"\d+(?:\s*,\s*\d+)*", body):
                        parsed = [int(n) for n in body.split(',')]
                    else:
                        invalid(f"Не распознана строка «{clause.strip()}». Пример: Итоги: 202")
                    if len(set(parsed)) != len(parsed) or any(n < 1 for n in parsed):
                        invalid("Итоговые строки должны быть уникальными номерами от 1")
                    if result.totals is not None and result.totals != parsed:
                        invalid("Противоречивые указания итоговых строк")
                    result.totals = parsed
                else:
                    parsed = None
                    if not (key == 'шапка' and body in ('нет', 'отсутствует')):
                        match = re.fullmatch(r"(\d+)(?:\s*[-–—]\s*(\d+))?", body)
                        if not match:
                            invalid(f"Не распознана строка «{clause.strip()}». Укажите диапазон, например 8–201")
                        parsed = (int(match[1]), int(match[2] or match[1]))
                        if not 1 <= parsed[0] <= parsed[1]:
                            invalid(f"Неверный диапазон: {body}")
                    if key == 'шапка':
                        if result.header_set and result.header != parsed:
                            invalid("Противоречивые указания шапки")
                        result.header, result.header_set = parsed, True
                    else:
                        if result.data is not None and result.data != parsed:
                            invalid("Противоречивые указания диапазона данных")
                        result.data = parsed
                continue
            normalized = re.sub(r"\s*[:—–-]\s*", " ", value)
            updates = legacy_column_feedback(value) or legacy_column_feedback(normalized)
            if not updates:
                match = re.fullmatch(
                    r"(счет|наименование счета|(?:сальдо|остаток|остатки) на (?:начало|конец) (?:дт|кт)|оборот(?:ы)? (?:дт|кт))"
                    r"\s+(?:(?:из\s+)?(?:колонка|колонки|столбец|столбца)\s+)?([a-zа-я]{1,3}|\d{1,5})", normalized)
                if match:
                    label, col = match.groups()
                    key = ('account' if label == 'счет' else 'account_name' if label == 'наименование счета'
                           else ('turnover' if label.startswith('оборот') else 'closing' if 'конец' in label else 'opening')
                           + ('_debit' if label.endswith('дт') else '_credit'))
                    updates = {key: _column(col)}
            if not updates:
                unknown.append(clause.strip())
                structured |= bool(re.match(r"^(счет|наименование счета|сальдо|оборот|остатки)\s*[:—–-]", value))
                continue
            structured = True
            for key, col in updates.items():
                if col is None:
                    invalid(f"Недопустимая колонка: {clause.strip()}")
                if key in result.mapping and result.mapping[key] != col:
                    invalid(f"Противоречивые указания колонок для поля {key}")
                result.mapping[key] = col
    if unknown and structured:
        invalid("Не распознана строка комментария: «" + unknown[0] + "». Исправьте её или отправьте свободный комментарий отдельно.")
    if len(set(result.mapping.values())) != len(result.mapping):
        invalid("Одна колонка назначена разным полям")
    return result


def apply_bounds(spec, instructions, sheet):
    spec = spec.model_copy(deep=True)
    spec.explicit_bounds = spec.explicit_bounds or instructions.has_bounds
    if instructions.data:
        spec.data_start_row, spec.data_end_row = instructions.data
    if instructions.header_set:
        spec.header_start_row, spec.header_end_row = instructions.header or (None, None)
        spec.header_row = instructions.header[1] if instructions.header else max(1, spec.data_start_row - 1)
        spec.header_absent = instructions.header is None
        if instructions.header and instructions.data is None:
            spec.data_start_row = instructions.header[1] + 1
    if instructions.totals is not None:
        spec.control_rows = list(instructions.totals)
    if not 1 <= spec.data_start_row <= spec.data_end_row <= sheet.max_row:
        invalid(f"Данные {spec.data_start_row}–{spec.data_end_row} выходят за границы листа (1–{sheet.max_row})")
    header_end = spec.header_end_row
    if not spec.header_absent and header_end is not None and header_end >= spec.data_start_row:
        invalid(f"Шапка заканчивается на строке {header_end}, данные начинаются на {spec.data_start_row}: диапазоны пересекаются. Уточните шапку или укажите «Шапка: отсутствует».")

    if any(n > sheet.max_row or n <= spec.data_end_row for n in spec.control_rows):
        invalid("Итоговые строки должны находиться после данных и в пределах листа")
    return spec
