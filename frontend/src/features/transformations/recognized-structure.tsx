import type { RecognitionSource, RecognizedStructure } from "../../lib/types";

const sourceLabels: Record<RecognitionSource, string> = {
  RULES: "Детерминированные правила",
  LLM: "LLM",
  USER: "Указано пользователем",
  MEMORY: "Память обработки",
};

function rowRange(start: number | null, end: number | null) {
  if (start == null) return "Определяется…";
  if (end == null || start === end) return `строка ${start}`;
  return `строки ${start}–${end}`;
}

function rowsLabel(rows: number[]) {
  return rows.length ? rows.join(", ") : "—";
}

export function RecognizedStructurePanel({ structure }: { structure: RecognizedStructure }) {
  return (
    <section className="mt-5 border-t pt-5" aria-labelledby="recognized-structure-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="recognized-structure-title" className="text-sm font-semibold text-navy">Распознанная структура</h3>
        <span className="text-xs text-slate-500">Лист «{structure.sheet_name}»</span>
      </div>

      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded bg-slate-50 px-3 py-2"><dt className="text-slate-500">Шапка</dt><dd className="mt-0.5 font-medium text-navy">{rowRange(structure.header_start_row, structure.header_end_row)}</dd></div>
        <div className="rounded bg-slate-50 px-3 py-2"><dt className="text-slate-500">Начало данных</dt><dd className="mt-0.5 font-medium text-navy">{structure.data_start_row == null ? "Определяется…" : `строка ${structure.data_start_row}`}</dd></div>
        <div className="rounded bg-slate-50 px-3 py-2"><dt className="text-slate-500">Конец данных</dt><dd className="mt-0.5 font-medium text-navy">{structure.data_end_row == null ? "Определяется…" : `строка ${structure.data_end_row}`}</dd></div>
        <div className="rounded bg-slate-50 px-3 py-2"><dt className="text-slate-500">Итоговые строки</dt><dd className="mt-0.5 font-medium text-navy">{rowsLabel(structure.control_rows)}</dd></div>
      </dl>

      {structure.service_rows.length > 0 && <p className="mt-2 text-xs text-slate-500">Служебный футер: строки {rowsLabel(structure.service_rows)}</p>}

      <div className="mt-3 overflow-x-auto rounded border">
        <table className="data-table min-w-full">
          <thead><tr><th>Целевое поле</th><th>Исходная колонка</th><th>Источник распознавания</th></tr></thead>
          <tbody>{structure.fields.map((field) => (
            <tr key={field.key}>
              <td className="font-medium text-navy">{field.label}</td>
              <td>{field.column == null ? <span className="text-slate-400">Не обнаружена</span> : <><span className="font-mono font-semibold">{field.column_letter}</span><span className="text-slate-500"> (колонка {field.column})</span></>}</td>
              <td>{field.source ? <span className="inline-flex rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-700">{sourceLabels[field.source]}</span> : <span className="text-slate-400">—</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}
