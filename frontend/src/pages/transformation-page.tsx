import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  Check,
  Download,
  FileSpreadsheet,
  RefreshCw,
  RotateCcw,
  Send,
  UploadCloud,
} from "lucide-react";
import { useEffect, useMemo, useState, type DragEvent } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/data-state";
import { Select, Textarea } from "../components/ui/input";
import { useToast } from "../components/ui/toast";
import { api, apiWithMeta, downloadResult } from "../lib/api";
import type { DataType, PreviewResponse, TemplateInfo, TransformationDetail, TransformationSummary } from "../lib/types";

const stages = [
  ["PROFILING", "Анализ структуры файла"],
  ["PLANNING", "Определение колонок и группировок"],
  ["TRANSFORMING", "Формирование правил и преобразование"],
  ["VALIDATING", "Контроль результата"],
  ["EXPORTING", "Формирование выходного файла"],
] as const;

const typeLabels: Record<DataType, string> = { TRIAL_BALANCE: "Оборотно-сальдовая ведомость", JOURNAL: "Журнал проводок" };

function formatDate(value: string) { return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)); }
function formatSize(bytes: number) { return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} КБ` : `${(bytes / 1024 / 1024).toFixed(1)} МБ`; }

export function TransformationPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [dataType, setDataType] = useState<DataType>("TRIAL_BALANCE");
  const [templateId, setTemplateId] = useState("trial_balance_flat_v1");
  const [selectedId, setSelectedId] = useState<string>();
  const [feedback, setFeedback] = useState("");

  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateInfo[]>("/transformations/templates") });
  const history = useQuery({ queryKey: ["transformations"], queryFn: () => apiWithMeta<TransformationSummary[]>("/transformations?page_size=50") });
  const current = useQuery({
    queryKey: ["transformation", selectedId],
    queryFn: () => api<TransformationDetail>(`/transformations/${selectedId}`),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => query.state.data?.status === "RUNNING" || query.state.data?.status === "QUEUED" ? 1000 : false,
  });
  const preview = useQuery({
    queryKey: ["preview", selectedId, current.data?.version],
    queryFn: () => api<PreviewResponse>(`/transformations/${selectedId}/preview`),
    enabled: current.data?.status === "COMPLETED" && current.data.version > 0,
  });

  useEffect(() => {
    if (current.data?.status === "COMPLETED" || current.data?.status === "FAILED") {
      void queryClient.invalidateQueries({ queryKey: ["transformations"] });
    }
  }, [current.data?.status, queryClient]);

  const availableTemplates = useMemo(() => templates.data?.filter((item) => item.data_type === dataType) ?? [], [templates.data, dataType]);
  const selectedTemplate = templates.data?.find((item) => item.id === templateId);
  const chooseType = (value: DataType) => {
    setDataType(value);
    const matching = templates.data?.find((item) => item.data_type === value);
    setTemplateId(matching?.id ?? (value === "TRIAL_BALANCE" ? "trial_balance_flat_v1" : "journal_flat_v1"));
  };
  const acceptFile = (candidate?: File) => {
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".xlsx")) { toast.show("Поддерживаются только файлы XLSX", "error"); return; }
    if (candidate.size > 25 * 1024 * 1024) { toast.show("Максимальный размер файла — 25 МБ", "error"); return; }
    setFile(candidate);
  };
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); acceptFile(event.dataTransfer.files[0]); };

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Выберите файл");
      const body = new FormData();
      body.append("file", file);
      body.append("data_type", dataType);
      body.append("template_id", templateId);
      return api<TransformationDetail>("/transformations", { method: "POST", body });
    },
    onSuccess: (item) => { setSelectedId(item.id); setFeedback(""); toast.show("Файл принят в обработку"); void queryClient.invalidateQueries({ queryKey: ["transformations"] }); },
    onError: (error) => toast.show(error.message, "error"),
  });
  const retry = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => api<TransformationDetail>(`/transformations/${id}/retry`, { method: "POST", body: JSON.stringify({ feedback: note || null }) }),
    onSuccess: (item) => { setSelectedId(item.id); setFeedback(""); toast.show("Повторная обработка запущена"); void queryClient.invalidateQueries({ queryKey: ["transformation", item.id] }); },
    onError: (error) => toast.show(error.message, "error"),
  });
  const download = async (item: TransformationSummary) => {
    try { await downloadResult(item.id, `${item.source_filename.replace(/\.xlsx$/i, "")}-result.xlsx`); }
    catch (error) { toast.show(error instanceof Error ? error.message : "Не удалось скачать файл", "error"); }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-xs font-bold uppercase tracking-widest text-action">Рабочая область преобразования</p><h1 className="mt-1 font-display text-3xl font-bold text-navy">Конструктор расшифровок</h1><p className="mt-1 text-sm text-slate-500">Преобразование нестандартных бухгалтерских выгрузок в плоские таблицы</p></div>
        <div className="flex items-center gap-2 rounded border bg-white px-3 py-2 text-xs text-slate-600"><Bot className="h-4 w-4 text-action" />Ollama · max-gpu-qwen:latest</div>
      </header>

      <div className="grid gap-6 xl:grid-cols-[1.15fr_.85fr]">
        <Card>
          <CardHeader title="Новое преобразование" description="Загрузите исходную книгу Excel и выберите целевую структуру" />
          <div className="space-y-5 p-5">
            <div
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
              className={`relative flex min-h-40 flex-col items-center justify-center rounded-lg border-2 border-dashed px-5 py-8 text-center transition ${dragging ? "border-action bg-blue-50" : file ? "border-emerald-300 bg-emerald-50/40" : "border-slate-300 bg-slate-50/60 hover:border-action"}`}
            >
              <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="absolute inset-0 cursor-pointer opacity-0" onChange={(event) => acceptFile(event.target.files?.[0])} />
              {file ? <><FileSpreadsheet className="h-8 w-8 text-emerald-600" /><p className="mt-3 text-sm font-semibold text-navy">{file.name}</p><p className="mt-1 text-xs text-slate-500">{formatSize(file.size)} · нажмите, чтобы заменить</p></> : <><UploadCloud className="h-8 w-8 text-action" /><p className="mt-3 text-sm font-semibold text-navy">Перетащите XLSX сюда</p><p className="mt-1 text-xs text-slate-500">или нажмите для выбора · не более 25 МБ</p></>}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div><label className="field-label">Тип исходных данных</label><Select value={dataType} onChange={(event) => chooseType(event.target.value as DataType)}><option value="TRIAL_BALANCE">Оборотно-сальдовая ведомость</option><option value="JOURNAL">Журнал проводок</option></Select></div>
              <div><label className="field-label">Целевой формат</label><Select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>{availableTemplates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></div>
            </div>
            {selectedTemplate && <div className="rounded border bg-slate-50 p-3"><p className="text-sm text-slate-600">{selectedTemplate.description}</p><p className="mt-2 text-xs text-slate-500">Поля: {selectedTemplate.columns.map((column) => column.label).join(" · ")}</p></div>}
            <Button className="w-full md:w-auto" disabled={!file || !templateId} loading={upload.isPending} onClick={() => upload.mutate()}><RefreshCw className="h-4 w-4" />Преобразовать файл</Button>
          </div>
        </Card>

        <Card>
          <CardHeader title="Статус обработки" description={current.data ? current.data.source_filename : "Этапы появляются после загрузки файла"} />
          {!selectedId ? <EmptyState title="Обработка не запущена" description="Выберите XLSX-файл и целевой формат." /> : current.isLoading ? <SkeletonRows count={5} /> : current.isError ? <div className="p-5"><ErrorState message={current.error.message} retry={() => void current.refetch()} /></div> : current.data && (
            <div className="p-5">
              <div className="mb-5 flex items-center justify-between"><Badge value={current.data.status} /><span className="text-xs text-slate-500">Версия {current.data.version || "—"}</span></div>
              <div className="space-y-1">
                {stages.map(([key, label], index) => {
                  const activeIndex = stages.findIndex(([stage]) => stage === current.data?.current_stage);
                  const done = current.data?.current_stage === "COMPLETED" || activeIndex > index;
                  const active = current.data?.current_stage === key;
                  return <div key={key} className={`flex items-center gap-3 rounded px-3 py-2.5 text-sm ${active ? "bg-blue-50 text-blue-800" : done ? "text-emerald-700" : "text-slate-400"}`}><div className={`flex h-6 w-6 items-center justify-center rounded-full border ${active ? "border-action bg-action text-white" : done ? "border-emerald-500 bg-emerald-50" : "border-slate-300"}`}>{done ? <Check className="h-3.5 w-3.5" /> : active ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : index + 1}</div><span className={active || done ? "font-medium" : ""}>{label}</span></div>;
                })}
              </div>
              {current.data.error_message && <div className="mt-5 flex gap-2 border border-red-200 bg-red-50 p-3 text-sm text-red-700"><AlertTriangle className="h-5 w-5 shrink-0" />{current.data.error_message}</div>}
            </div>
          )}
        </Card>
      </div>

      {current.data?.status === "COMPLETED" && (
        <Card className="overflow-hidden">
          <CardHeader title="Результат преобразования" description={`${typeLabels[current.data.data_type]} · версия ${current.data.version}`} action={<div className="flex gap-2"><Button size="sm" variant="secondary" loading={retry.isPending} onClick={() => retry.mutate({ id: current.data!.id })}><RotateCcw className="h-4 w-4" />Обработать повторно</Button><Button size="sm" onClick={() => void download(current.data!)}><Download className="h-4 w-4" />Скачать Excel</Button></div>} />
          <div className="grid gap-px border-b bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Обработано записей</p><p className="mt-1 font-mono text-2xl font-semibold text-navy">{current.data.processed_rows}</p></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Не обработано строк</p><p className="mt-1 font-mono text-2xl font-semibold text-navy">{current.data.unprocessed_rows}</p></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Контроль результата</p><div className="mt-2"><Badge value={current.data.control_status} /></div></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Планировщик</p><p className="mt-2 text-sm font-semibold text-navy">{current.data.llm_used ? "Ollama + правила" : "Детерминированные правила"}</p></div>
          </div>
          {current.data.warnings.length > 0 && <div className="border-b bg-amber-50 px-5 py-4"><div className="flex gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" /><div><p className="text-sm font-semibold text-amber-900">Замечания контроля</p><ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-amber-800">{current.data.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div></div>}
          {preview.isLoading ? <SkeletonRows count={6} /> : preview.isError ? <div className="p-5"><ErrorState message={preview.error.message} /></div> : preview.data && <div className="overflow-x-auto"><table className="data-table"><thead><tr>{preview.data.columns.map((column) => <th key={column.key}>{column.label}</th>)}<th>Строка источника</th></tr></thead><tbody>{preview.data.rows.map((row, index) => <tr key={index}>{preview.data.columns.map((column) => <td key={column.key} className={column.kind === "number" ? "whitespace-nowrap text-right font-mono text-xs" : "min-w-36"}>{row[column.key] == null ? <span className="text-slate-300">—</span> : String(row[column.key])}</td>)}<td className="font-mono text-xs text-slate-500">{String(row._source_row ?? "")}</td></tr>)}</tbody></table><div className="border-t bg-slate-50 px-5 py-3 text-xs text-slate-500">Показаны первые {preview.data.rows.length} из {preview.data.total} записей</div></div>}
        </Card>
      )}

      {current.data?.status === "COMPLETED" && (
        <Card>
          <CardHeader title="Замечания к результату" description="Локальная модель изменит спецификацию и создаст новую версию результата" />
          <div className="p-5"><label className="field-label" htmlFor="feedback">Что необходимо исправить в результате?</label><Textarea id="feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="Например: контрагенты после строки 150 относятся к счету 62.02, а не 62.01" /><div className="mt-3 flex items-center justify-between gap-4"><p className="text-xs text-slate-500">Данные обрабатываются локально моделью max-gpu-qwen:latest.</p><Button disabled={!feedback.trim()} loading={retry.isPending} onClick={() => retry.mutate({ id: current.data!.id, note: feedback.trim() })}><Send className="h-4 w-4" />Исправить и обработать повторно</Button></div></div>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader title="История преобразований" description="Результаты сохраняются и доступны после обновления страницы" />
        {history.isLoading ? <SkeletonRows /> : history.isError ? <div className="p-5"><ErrorState message={history.error.message} retry={() => void history.refetch()} /></div> : history.data?.data.length ? <div className="overflow-x-auto"><table className="data-table"><thead><tr><th>Дата и время</th><th>Исходный файл</th><th>Тип данных</th><th>Записей</th><th>Статус</th><th>Контроль</th><th>Действия</th></tr></thead><tbody>{history.data.data.map((item) => <tr key={item.id}><td className="whitespace-nowrap font-mono text-xs">{formatDate(item.created_at)}</td><td className="max-w-72 truncate font-semibold text-navy">{item.source_filename}</td><td>{typeLabels[item.data_type]}</td><td className="font-mono">{item.processed_rows}</td><td><Badge value={item.status} /></td><td><Badge value={item.control_status} /></td><td><div className="flex whitespace-nowrap"><Button variant="ghost" size="sm" onClick={() => { setSelectedId(item.id); window.scrollTo({ top: 0, behavior: "smooth" }); }}>Открыть</Button>{item.version > 0 && <Button variant="ghost" size="sm" onClick={() => void download(item)}><Download className="h-3.5 w-3.5" />Excel</Button>}<Button variant="ghost" size="sm" disabled={item.status === "RUNNING" || item.status === "QUEUED"} onClick={() => retry.mutate({ id: item.id })}><RotateCcw className="h-3.5 w-3.5" />Повторить</Button></div></td></tr>)}</tbody></table></div> : <EmptyState title="История пуста" description="Первое преобразование появится здесь после загрузки файла." />}
      </Card>
    </div>
  );
}

