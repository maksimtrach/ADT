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
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/data-state";
import { Select, Textarea } from "../components/ui/input";
import { useToast } from "../components/ui/toast";
import { TechnicalMemo } from "../features/transformations/technical-memo";
import { api, ApiError, downloadResult } from "../lib/api";
import type { DataType, PreviewResponse, SourcePreview, TemplateInfo, TransformationDetail, TransformationSummary } from "../lib/types";

const stages = [
  ["PROFILING", "Анализ структуры файла"],
  ["PLANNING", "Определение колонок и группировок"],
  ["TRANSFORMING", "Формирование правил и преобразование"],
  ["VALIDATING", "Контроль результата"],
  ["EXPORTING", "Формирование выходного файла"],
] as const;

const typeLabels: Record<DataType, string> = { TRIAL_BALANCE: "Оборотно-сальдовая ведомость", JOURNAL: "Журнал проводок" };

function formatSize(bytes: number) { return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} КБ` : `${(bytes / 1024 / 1024).toFixed(1)} МБ`; }

export function TransformationPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { transformationId: selectedId } = useParams();
  const [file, setFile] = useState<File | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [sourcePreviewLoading, setSourcePreviewLoading] = useState(false);
  const [sourcePreviewError, setSourcePreviewError] = useState<string | null>(null);
  const [selectedSourceSheet, setSelectedSourceSheet] = useState("");
  const [sheetLoading, setSheetLoading] = useState(false);
  const uploadGeneration = useRef(0);
  const sheetGeneration = useRef(0);
  const stagedUploadId = useRef<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [dataType, setDataType] = useState<DataType>("TRIAL_BALANCE");
  const [templateId, setTemplateId] = useState("trial_balance_flat_v1");
  const [feedback, setFeedback] = useState("");

  useEffect(() => { setFeedback(""); }, [selectedId]);
  useEffect(() => () => {
    uploadGeneration.current += 1;
    sheetGeneration.current += 1;
    if (stagedUploadId.current) {
      void api<void>(`/transformations/source-uploads/${stagedUploadId.current}`, { method: "DELETE" }).catch(() => {});
    }
  }, []);

  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateInfo[]>("/transformations/templates") });
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
  const discardStagedUpload = (id: string) => {
    void api<void>(`/transformations/source-uploads/${id}`, { method: "DELETE" }).catch(() => {});
  };
  const acceptFile = async (candidate?: File) => {
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".xlsx")) { toast.show("Поддерживаются только файлы XLSX", "error"); return; }
    if (candidate.size > 25 * 1024 * 1024) { toast.show("Максимальный размер файла — 25 МБ", "error"); return; }
    const generation = ++uploadGeneration.current;
    sheetGeneration.current += 1;
    if (stagedUploadId.current) discardStagedUpload(stagedUploadId.current);
    stagedUploadId.current = null;
    setFile(candidate);
    setSourcePreview(null);
    setSelectedSourceSheet("");
    setSourcePreviewError(null);
    setSourcePreviewLoading(true);
    setSheetLoading(false);
    const body = new FormData();
    body.append("file", candidate);
    try {
      const result = await api<SourcePreview>("/transformations/source-uploads", { method: "POST", body });
      if (generation !== uploadGeneration.current) { discardStagedUpload(result.upload_id); return; }
      stagedUploadId.current = result.upload_id;
      setSourcePreview(result);
      setSelectedSourceSheet(result.sheet_name);
    } catch (error) {
      if (generation === uploadGeneration.current) setSourcePreviewError(error instanceof Error ? error.message : "Не удалось прочитать XLSX-файл");
    } finally {
      if (generation === uploadGeneration.current) setSourcePreviewLoading(false);
    }
  };
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); void acceptFile(event.dataTransfer.files[0]); };
  const chooseSourceSheet = async (sheet: string) => {
    if (!sourcePreview) return;
    const generation = ++sheetGeneration.current;
    const id = sourcePreview.upload_id;
    setSelectedSourceSheet(sheet);
    setSheetLoading(true);
    setSourcePreviewError(null);
    try {
      const result = await api<SourcePreview>(`/transformations/source-uploads/${id}/preview?sheet=${encodeURIComponent(sheet)}`);
      if (generation === sheetGeneration.current && stagedUploadId.current === id) setSourcePreview(result);
    } catch (error) {
      if (generation === sheetGeneration.current) {
        if (error instanceof ApiError && (error.code === "UPLOAD_EXPIRED" || error.code === "UPLOAD_NOT_FOUND")) {
          stagedUploadId.current = null;
          setSourcePreview(null);
          setSourcePreviewError("Предварительная загрузка недоступна. Выберите файл ещё раз.");
        } else {
          setSelectedSourceSheet(sourcePreview.sheet_name);
          setSourcePreviewError(error instanceof Error ? error.message : "Не удалось прочитать лист");
        }
      }
    } finally {
      if (generation === sheetGeneration.current) setSheetLoading(false);
    }
  };

  const upload = useMutation({
    mutationFn: async () => {
      if (!sourcePreview) throw new Error("Дождитесь предварительной загрузки файла");
      const body = new FormData();
      body.append("upload_id", sourcePreview.upload_id);
      body.append("data_type", dataType);
      body.append("template_id", templateId);
      return api<TransformationDetail>("/transformations", { method: "POST", body });
    },
    onSuccess: (item) => {
      uploadGeneration.current += 1;
      sheetGeneration.current += 1;
      stagedUploadId.current = null;
      setFile(null);
      setSourcePreview(null);
      setSelectedSourceSheet("");
      navigate(`/transformations/${item.id}`);
      setFeedback("");
      toast.show("Файл принят в обработку");
      void queryClient.invalidateQueries({ queryKey: ["transformations"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.code === "UPLOAD_EXPIRED" || error.code === "UPLOAD_NOT_FOUND" || error.code === "UPLOAD_CONSUMED")) {
        stagedUploadId.current = null;
        setSourcePreview(null);
        setSourcePreviewError("Предварительная загрузка недоступна. Выберите файл ещё раз.");
      }
      toast.show(error.message, "error");
    },
  });
  const retry = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => api<TransformationDetail>(`/transformations/${id}/retry`, { method: "POST", body: JSON.stringify({ feedback: note || null }) }),
    onSuccess: (item) => {
      // Keep the comment until the user edits it, including after a failed run.
      // Apply QUEUED immediately so buttons cannot launch a duplicate request.
      queryClient.setQueryData(["transformation", item.id], item);
      toast.show("Повторная обработка запущена");
      void queryClient.invalidateQueries({ queryKey: ["transformation", item.id] });
      void queryClient.invalidateQueries({ queryKey: ["transformation-memo", item.id] });
      void queryClient.invalidateQueries({ queryKey: ["transformations"] });
    },
    onError: (error) => toast.show(error.message, "error"),
  });
  const download = async (item: TransformationSummary) => {
    try { await downloadResult(item.id, `${item.source_filename.replace(/\.xlsx$/i, "")}-result.xlsx`); }
    catch (error) { toast.show(error instanceof Error ? error.message : "Не удалось скачать файл", "error"); }
  };
  const processing = current.data?.status === "QUEUED" || current.data?.status === "RUNNING";
  const retryDisabled = retry.isPending || processing || !current.data;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-xs font-bold uppercase tracking-widest text-action">Рабочая область преобразования</p><h1 className="mt-1 font-display text-3xl font-bold text-navy">Конструктор расшифровок</h1><p className="mt-1 text-sm text-slate-500">Преобразование нестандартных бухгалтерских выгрузок в плоские таблицы</p></div>
        <div className="flex items-center gap-2 rounded border bg-white px-3 py-2 text-xs text-slate-600"><Bot className="h-4 w-4 text-action" />Правила · LLM при необходимости</div>
      </header>

      <Card>
          <CardHeader title="1. Загрузка файла" description="Выберите исходную книгу Excel" />
          <div className="p-5">
            <div
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
              className={`relative flex min-h-40 flex-col items-center justify-center rounded-lg border-2 border-dashed px-5 py-8 text-center transition ${dragging ? "border-action bg-blue-50" : file ? "border-emerald-300 bg-emerald-50/40" : "border-slate-300 bg-slate-50/60 hover:border-action"}`}
            >
              <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="absolute inset-0 cursor-pointer opacity-0" onChange={(event) => { void acceptFile(event.target.files?.[0]); event.target.value = ""; }} />
              {file ? <><FileSpreadsheet className="h-8 w-8 text-emerald-600" /><p className="mt-3 text-sm font-semibold text-navy">{file.name}</p><p className="mt-1 text-xs text-slate-500">{formatSize(file.size)} · нажмите, чтобы заменить</p></> : <><UploadCloud className="h-8 w-8 text-action" /><p className="mt-3 text-sm font-semibold text-navy">Перетащите XLSX сюда</p><p className="mt-1 text-xs text-slate-500">или нажмите для выбора · не более 25 МБ</p></>}
            </div>
            {file && <div className="mt-5 space-y-3">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-navy">Исходные строки</h3>
                  <p className="text-xs text-slate-500">Первые 100 строк книги до преобразования</p>
                </div>
                {sourcePreview && sourcePreview.sheets.length > 1 && <div className="min-w-48">
                  <label className="field-label" htmlFor="source-sheet">Лист для просмотра</label>
                  <Select id="source-sheet" value={selectedSourceSheet} disabled={sheetLoading} onChange={(event) => void chooseSourceSheet(event.target.value)}>
                    {sourcePreview.sheets.map((sheet) => <option key={sheet} value={sheet}>{sheet}</option>)}
                  </Select>
                </div>}
              </div>
              {(sourcePreviewLoading || sheetLoading) && <SkeletonRows count={5} />}
              {sourcePreviewError && <div className="flex flex-wrap items-center gap-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                <span>{sourcePreviewError}</span>
                {!sourcePreview && <Button size="sm" variant="secondary" onClick={() => void acceptFile(file)}>Повторить загрузку</Button>}
              </div>}
              {sourcePreview && !sheetLoading && <>
                <div className="max-h-[34rem] overflow-auto rounded border" role="region" aria-label="Предпросмотр исходного файла" tabIndex={0}>
                  <table className="data-table min-w-max">
                    <thead><tr><th className="sticky left-0 top-0 z-20 bg-slate-50">№</th>{sourcePreview.columns.map((column) => <th key={column} className="sticky top-0 z-10 bg-slate-50">{column}</th>)}</tr></thead>
                    <tbody>{sourcePreview.rows.map((row) => <tr key={row.row}>
                      <th scope="row" className="sticky left-0 z-10 bg-white font-mono text-xs">{row.row}</th>
                      {row.cells.map((value, index) => <td key={sourcePreview.columns[index]} className="max-w-72 min-w-28 truncate whitespace-nowrap text-xs" title={value == null ? undefined : String(value)}>{value == null ? <span className="text-slate-300">—</span> : String(value)}</td>)}
                    </tr>)}</tbody>
                  </table>
                </div>
                <p className="text-xs text-slate-500">Лист «{sourcePreview.sheet_name}»: показаны первые {sourcePreview.rows.length} из {sourcePreview.total_rows} строк{sourcePreview.columns_truncated ? " · предпросмотр ограничен первыми 120 колонками" : ""}.</p>
              </>}
            </div>}
          </div>
      </Card>

      <Card>
          <CardHeader title="2. Параметры преобразования" description="Укажите тип исходных данных и целевой формат" />
          <div className="space-y-5 p-5">
            <div className="grid gap-4 md:grid-cols-2">
              <div><label className="field-label">Тип исходных данных</label><Select value={dataType} onChange={(event) => chooseType(event.target.value as DataType)}><option value="TRIAL_BALANCE">Оборотно-сальдовая ведомость</option><option value="JOURNAL">Журнал проводок</option></Select></div>
              <div><label className="field-label">Целевой формат</label><Select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>{availableTemplates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></div>
            </div>
            {selectedTemplate && <div className="rounded border bg-slate-50 p-3"><p className="text-sm text-slate-600">{selectedTemplate.description}</p><p className="mt-2 text-xs text-slate-500">Поля: {selectedTemplate.columns.map((column) => column.label).join(" · ")}</p></div>}
            <Button className="w-full md:w-auto" disabled={!sourcePreview || !templateId || sourcePreviewLoading || sheetLoading || upload.isPending} loading={upload.isPending} onClick={() => upload.mutate()}><RefreshCw className="h-4 w-4" />Преобразовать</Button>
          </div>
        </Card>

      <Card>
          <CardHeader title="3. Статус преобразования" description={current.data ? current.data.source_filename : "Этапы появляются после загрузки файла"} />
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
      <Card className="min-w-0 overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b px-5 py-4">
            <div>
              <h2 className="font-display text-lg font-semibold text-navy">4. Результаты преобразования</h2>
              <p className="mt-1 text-sm text-slate-500">{current.data?.status === "COMPLETED" ? `${typeLabels[current.data.data_type]} · версия ${current.data.version}` : "Первые строки преобразованного файла"}</p>
            </div>
            {current.data?.status === "COMPLETED" && <Button size="sm" className="w-full sm:w-auto" onClick={() => { if (current.data) void download(current.data); }}><Download className="h-4 w-4" />Выгрузить в Excel</Button>}
          </div>
          {current.data?.status === "COMPLETED" ? <>
          <div className="grid gap-px border-b bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Обработано записей</p><p className="mt-1 font-mono text-2xl font-semibold text-navy">{current.data.processed_rows}</p></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Не обработано строк</p><p className="mt-1 font-mono text-2xl font-semibold text-navy">{current.data.unprocessed_rows}</p></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Контроль результата</p><div className="mt-2"><Badge value={current.data.control_status} /></div></div>
            <div className="bg-white p-5"><p className="text-xs text-slate-500">Планировщик</p><p className="mt-2 text-sm font-semibold text-navy">{current.data.llm_used ? "LLM + правила" : "Детерминированные правила"}</p></div>
          </div>
          {current.data.warnings.length > 0 && <div className="border-b bg-amber-50 px-5 py-4"><div className="flex gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" /><div><p className="text-sm font-semibold text-amber-900">Замечания контроля</p><ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-amber-800">{current.data.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div></div>}
          {preview.isLoading ? <SkeletonRows count={6} /> : preview.isError ? <div className="p-5"><ErrorState message={preview.error.message} /></div> : preview.data && <><div className="max-h-[32rem] overflow-auto" role="region" aria-label="Первые строки результата" tabIndex={0}><table className="data-table"><thead><tr>{preview.data.columns.map((column) => <th key={column.key} className="sticky top-0 z-10 bg-slate-50">{column.label}</th>)}<th className="sticky top-0 z-10 bg-slate-50">Строка источника</th></tr></thead><tbody>{preview.data.rows.map((row, index) => <tr key={index}>{preview.data.columns.map((column) => <td key={column.key} className={column.kind === "number" ? "whitespace-nowrap text-right font-mono text-xs" : "min-w-36"}>{row[column.key] == null ? <span className="text-slate-300">—</span> : String(row[column.key])}</td>)}<td className="font-mono text-xs text-slate-500">{String(row._source_row ?? "")}</td></tr>)}</tbody></table></div><div className="border-t bg-slate-50 px-5 py-3 text-xs text-slate-500">Показаны первые {preview.data.rows.length} из {preview.data.total} записей</div></>}
          </> : <EmptyState title="Результат пока не готов" description={current.data?.status === "FAILED" ? "Обработка завершилась с ошибкой. Уточните правила в блоке корректировок." : processing ? "Преобразование выполняется." : "Результат появится после преобразования файла."} />}
        </Card>

      <Card>
          <CardHeader
            title="5. Корректировки"
            description={current.data?.status === "FAILED"
              ? "Уточните структуру файла или правила обработки. Загружать файл заново не требуется."
              : processing ? "Обработка выполняется. Повторный запуск будет доступен после её завершения."
              : current.data ? "Уточните правила или повторите обработку сохранённого исходного файла." : "Корректировки станут доступны после первой обработки."}
          />
          <div className="space-y-3 p-5">
            <div>
              <label className="field-label" htmlFor="feedback">Комментарий к обработке</label>
              <Textarea id="feedback" value={feedback} disabled={retryDisabled} onChange={(event) => setFeedback(event.target.value)} placeholder="Например: Брать счета из колонки C" />
            </div>
            <p className="text-xs text-slate-500">Явное указание колонки применяется без LLM. Для других замечаний используется настроенный профиль модели. При ошибке комментарий останется в поле для редактирования.</p>
            <div className="flex flex-wrap gap-3">
              <Button variant="secondary" disabled={retryDisabled} onClick={() => { if (current.data) retry.mutate({ id: current.data.id }); }}><RotateCcw className="h-4 w-4" />Повторить без изменений</Button>
              <Button disabled={retryDisabled || !feedback.trim()} loading={retry.isPending} onClick={() => { if (current.data) retry.mutate({ id: current.data.id, note: feedback.trim() }); }}><Send className="h-4 w-4" />Повторить с комментарием</Button>
            </div>
          </div>
        {current.data && <TechnicalMemo transformationId={current.data.id} status={current.data.status} />}
      </Card>
    </div>
  );
}
