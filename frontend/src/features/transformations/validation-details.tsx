import type { ValidationDetails } from "../../lib/types";

const labels: Record<string, string> = {
  opening_debit: "Сальдо на начало Дт", opening_credit: "Сальдо на начало Кт",
  turnover_debit: "Оборот Дт", turnover_credit: "Оборот Кт",
  closing_debit: "Сальдо на конец Дт", closing_credit: "Сальдо на конец Кт",
};
const money = (value: number | null) => value == null ? "—" : value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function ValidationDetailsPanel({ validation }: { validation: ValidationDetails }) {
  const balance = validation.row_balance;
  const comparisons = Object.entries(validation.control_comparisons || {});
  return <section className="mt-5 border-t pt-4">
    <h3 className="text-sm font-semibold text-navy">Проверка сумм и строк</h3>
    {validation.accepted_with_errors && <p className="mt-2 rounded bg-amber-50 p-3 text-sm text-amber-900">Результат загружен с ошибками по вашему решению. Расхождения сохранены.</p>}
    <p className="mt-2 text-xs text-slate-600">Итоговые строки исходного листа: {validation.control_rows?.join(", ") || "не обнаружены"}. Суммирование: {validation.aggregation === "root_accounts" ? "верхний уровень счетов" : validation.aggregation === "two_digit_root_accounts" ? "двухзначные корневые счета" : "все строки данных"}.</p>
    {comparisons.length > 0 && <div className="mt-3 overflow-auto"><table className="data-table min-w-full">
      <thead><tr><th>Показатель</th><th>Итог источника</th><th>Рассчитано</th><th>Разница</th><th>Проверка</th></tr></thead>
      <tbody>{comparisons.map(([key, item]) => <tr key={key} className={item.passed ? "" : "bg-red-50 text-red-800"}>
        <td>{labels[key] || key}</td><td>{money(item.declared)}</td><td>{money(item.calculated)}</td><td>{money(item.difference)}</td><td>{item.passed ? "Совпадает" : "Расхождение"}</td>
      </tr>)}</tbody>
    </table></div>}
    {balance && <div className="mt-4 text-sm">
      <p>Построчная формула: начало Дт − начало Кт + оборот Дт − оборот Кт − конец Дт + конец Кт = 0.</p>
      <p className="mt-1">Проверено строк: {balance.checked}. Нарушений: {balance.failed}. Допуск: 0,01.</p>
      {balance.failed > 0 && <div className="mt-2 max-h-64 overflow-auto" aria-label="Строки с нарушением формулы"><table className="data-table min-w-full">
        <thead><tr><th>Строка исходного листа</th><th>Отклонение от нуля</th></tr></thead>
        <tbody>{balance.examples.map((item) => <tr key={item.row}><td>{item.row}</td><td>{money(item.difference)}</td></tr>)}</tbody>
      </table></div>}
      {balance.failed > balance.examples.length && <p className="mt-1 text-xs text-slate-500">Для этой старой обработки сохранены только первые {balance.examples.length} нарушений. Повторная обработка сохранит полный список.</p>}
    </div>}
  </section>;
}
