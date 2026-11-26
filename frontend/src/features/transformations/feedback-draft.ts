import type { RecognizedStructure } from "../../lib/types";

const fields = [
  ["account", "Счет"], ["account_name", "Наименование счета"],
  ["opening_debit", "Сальдо на начало Дт"], ["opening_credit", "Сальдо на начало Кт"],
  ["turnover_debit", "Оборот Дт"], ["turnover_credit", "Оборот Кт"],
  ["closing_debit", "Сальдо на конец Дт"], ["closing_credit", "Сальдо на конец Кт"],
];

export function feedbackDraft(structure?: RecognizedStructure | null): string {
  const lines = fields.map(([key, label]) => {
    const field = structure?.fields.find((item) => item.key === key);
    return `${label}: ${field?.column_letter ?? field?.column ?? ""}`;
  });
  const range = (start?: number | null, end?: number | null) =>
    start != null || end != null ? `${start ?? ""}–${end ?? ""}` : "";
  lines.push(`Шапка: ${range(structure?.header_start_row, structure?.header_end_row)}`);
  lines.push(`Данные: ${range(structure?.data_start_row, structure?.data_end_row)}`);
  lines.push(`Итоги: ${structure?.control_rows.join(", ") ?? ""}`);
  return lines.join("\n");
}
