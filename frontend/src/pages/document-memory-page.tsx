import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { ErrorState, SkeletonRows } from "../components/ui/data-state";
import { Select } from "../components/ui/input";
import { useToast } from "../components/ui/toast";
import { useAuth } from "../features/auth/auth-context";
import { api, downloadFile } from "../lib/api";
import type { DocumentProfile, MemoryCase, MemoryRecord } from "../lib/types";

const statuses = { draft: "Черновик", approved: "Подтверждён", disabled: "Отключён" };
const outcomes = { resolved: "Решение сохранено", failed: "Ошибка обработки", unsupported: "Требует расширения правил памяти" };
const fields: Record<string, string> = {
  account: "Счёт", account_name: "Наименование счёта", subaccount: "Субсчёт", analytics: "Аналитика",
  opening_debit: "Начальное сальдо Дт", opening_credit: "Начальное сальдо Кт",
  turnover_debit: "Оборот Дт", turnover_credit: "Оборот Кт",
  closing_debit: "Конечное сальдо Дт", closing_credit: "Конечное сальдо Кт",
};
function columnName(value: number): string {
  let result = "";
  while (value > 0) { value--; result = String.fromCharCode(65 + value % 26) + result; value = Math.floor(value / 26); }
  return result;
}

export function DocumentMemoryPage() {
  const { user } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useState("all");
  const [exporting, setExporting] = useState(false);
  const records = useQuery({ queryKey: ["document-memory"], queryFn: () => api<MemoryRecord[]>("/document-memory") });
  const cases = useQuery({ queryKey: ["memory-cases"], queryFn: () => api<MemoryCase[]>("/document-memory/cases") });
  const profiles = useQuery({ queryKey: ["document-profiles"], queryFn: () => api<DocumentProfile[]>("/transformations/document-profiles") });
  const refresh = () => void client.invalidateQueries({ queryKey: ["document-memory"] });
  const status = useMutation({
    mutationFn: ({ record, value, scope }: { record: MemoryRecord; value: MemoryRecord["status"]; scope: MemoryRecord["scope"] }) =>
      api(`/document-memory/${record.card.id}/status`, { method: "POST", body: JSON.stringify({ revision: record.card.revision, status: value, scope }) }),
    onSuccess: () => { refresh(); toast.show("Состояние подхода обновлено"); },
    onError: (error: Error) => toast.show(error.message, "error"),
  });
  const importer = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > 20 * 1024 * 1024) throw new Error("Пакет памяти превышает 20 МБ");
      const body = new FormData(); body.append("file", file);
      return api<{ imported: number; skipped: number }>("/document-memory/import", { method: "POST", body });
    },
    onSuccess: (result) => { refresh(); toast.show(`Добавлено черновиков: ${result.imported}. Уже присутствуют: ${result.skipped}`); },
    onError: (error: Error) => toast.show(error.message, "error"),
  });
  async function exportMemory() {
    setExporting(true);
    try { await downloadFile("/document-memory/export", "document-memory.json"); }
    catch (error) { toast.show(error instanceof Error ? error.message : "Ошибка экспорта", "error"); }
    finally { setExporting(false); }
  }
  const visible = records.data?.filter((record) => filter === "all" || record.status === filter) ?? [];
  return <div className="space-y-6">
    <header><h1 className="font-display text-3xl font-bold text-navy">Память обработки</h1>
      <p className="mt-2 text-sm text-slate-500">Сохранённые подходы используются при затруднениях распознавания. Каждый новый файл проходит полную проверку.</p></header>
    <Card><CardHeader title="Перенос знаний" description="Экспорт содержит подходы и тексты шапок. Исходные файлы, суммы, комментарии и учётные записи в пакет не включаются." />
      <div className="space-y-3 p-5">
        <p className="text-sm text-slate-600">Перед передачей пакета проверьте, нет ли в шапках названий организаций. На другой установке импортированные подходы появятся как частные черновики и потребуют подтверждения.</p>
        <div className="flex flex-wrap gap-3"><Button variant="secondary" loading={exporting} disabled={records.isLoading || records.isError} onClick={() => void exportMemory()}>Экспортировать память</Button>
          <Button loading={importer.isPending} onClick={() => input.current?.click()}>Импортировать пакет</Button>
          <input ref={input} type="file" accept=".json,application/json" className="hidden" aria-label="Пакет памяти" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) importer.mutate(file); }} /></div>
      </div></Card>
    <Card><CardHeader title="Типы документов" description="Профили подготовки, распознавания и проверки" />
      <div className="flex flex-wrap gap-2 p-5">{profiles.data?.map((profile) => <span key={profile.id} className="rounded border px-3 py-2 text-sm">{profile.name} · {profile.available ? profile.memory_enabled ? "обработка и память" : "обработка" : "запланирован"}</span>)}
        {profiles.isError && <ErrorState message={profiles.error.message} retry={() => void profiles.refetch()} />}</div></Card>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-display text-xl font-semibold text-navy">Подходы</h2>
      <Select aria-label="Статус подходов" className="w-56" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">Все состояния</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select></div>
    {records.isLoading ? <SkeletonRows count={3} /> : records.isError ? <ErrorState message={records.error.message} retry={() => void records.refetch()} /> : !visible.length ? <p className="rounded border bg-white p-5 text-sm text-slate-500">Подходов пока нет. Они появятся после исправления сложной общей ОСВ или сохранения подхода из результата преобразования.</p> : visible.map((record) => {
      const card = record.card;
      const editable = user?.role === "ADMIN" || (record.owner_id === user?.id && record.scope === "private");
      const superseded = records.data?.some((r) => r.card.id === card.id && r.card.revision > card.revision);
      return <Card key={`${card.id}/${card.revision}`} className="overflow-hidden">
        <CardHeader title={`${card.title} · версия ${card.revision}`} description={`${statuses[record.status]} · ${record.scope === "shared" ? "Общий" : "Частный"}${superseded ? " · Есть более новая версия; эта версия не применяется" : ""}`} />
        <div className="space-y-4 p-5"><p className="text-sm text-slate-600">{card.description}</p>
          <p className="text-xs text-slate-500">При сохранении проверено строк: {card.verified_rows}. Независимые итоги: {card.independent_totals ? "совпали по всем шести показателям" : "полного подтверждения нет"}. Применений: {record.successes}; отклонений проверками: {record.rejections}.</p>
          <details><summary className="cursor-pointer text-sm font-semibold text-action">Шапка и правила сопоставления</summary>
            <div className="mt-3 max-h-64 overflow-auto"><table className="data-table"><thead><tr>{card.header[0]?.map((_, i) => <th key={i}>{columnName(i + 1)}</th>)}</tr></thead><tbody>{card.header.map((row, r) => <tr key={r}>{row.map((value, c) => <td key={c}>{value || "—"}</td>)}</tr>)}</tbody></table></div>
            <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(card.column_mapping).map(([field, column]) => <div key={field} className="flex gap-2"><dt>{fields[field] ?? field}:</dt><dd className="font-mono">{columnName(column)}</dd></div>)}</dl>
            <p className="mt-3 text-xs text-slate-500">Режим: {card.row_mode === "account_tree" ? "дерево счетов" : "плоская таблица"}. Номера строк и границы старого файла не переносятся. Идентификатор: {card.id}.</p>
          </details>
          {editable && <div className="flex flex-wrap gap-2">
            {record.status !== "approved" && !superseded && <Button size="sm" disabled={status.isPending} onClick={() => status.mutate({ record, value: "approved", scope: record.scope })}>Подтвердить подход</Button>}
            {record.status !== "disabled" && <Button size="sm" variant="secondary" disabled={status.isPending} onClick={() => status.mutate({ record, value: "disabled", scope: record.scope })}>Отключить</Button>}
            {user?.role === "ADMIN" && record.scope === "private" && record.status === "approved" && !superseded && <Button size="sm" variant="secondary" disabled={status.isPending} onClick={() => status.mutate({ record, value: "approved", scope: "shared" })}>Сделать общим</Button>}
            {user?.role === "ADMIN" && record.scope === "shared" && <Button size="sm" variant="secondary" disabled={status.isPending} onClick={() => status.mutate({ record, value: record.status, scope: "private" })}>Сделать частным</Button>}
          </div>}
        </div></Card>;
    })}
    <Card><CardHeader title="Последние сложные случаи" description="До 100 последних случаев. Диагностика остаётся на этой установке и не включается в экспорт подходов." />
      {cases.isLoading ? <SkeletonRows count={3} /> : cases.isError ? <div className="p-5"><ErrorState message={cases.error.message} retry={() => void cases.refetch()} /></div> : <div className="max-h-80 overflow-auto"><table className="data-table"><thead><tr><th>Дата</th><th>Результат</th><th>Причина</th><th>Обработка</th></tr></thead><tbody>{cases.data?.map((item) => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString("ru-RU")}</td><td>{outcomes[item.outcome]}</td><td>{item.reason}</td><td><Link className="text-action hover:underline" to={`/transformations/${item.transformation_id}`}>Открыть</Link></td></tr>)}</tbody></table>{!cases.data?.length && <p className="p-5 text-sm text-slate-500">Сложных случаев пока нет.</p>}</div>}
    </Card>
  </div>;
}
