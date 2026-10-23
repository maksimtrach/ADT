import { useQuery } from "@tanstack/react-query";
import { Clipboard, Download, ScrollText } from "lucide-react";
import { useMemo } from "react";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { ErrorState, SkeletonRows } from "../../components/ui/data-state";
import { useToast } from "../../components/ui/toast";
import { api, ApiError } from "../../lib/api";
import type { TransformationMemo, TransformationRunEvent, TransformationStatus } from "../../lib/types";

const eventLabels: Record<string, string> = {
  FILE_OPENED: "Файл открыт",
  PROFILE_CREATED: "Профиль книги сформирован",
  SOURCE_SAMPLE: "Первые строки исходного файла",
  BASELINE_SPEC_CREATED: "Базовые правила преобразования",
  BASELINE_CHECKED: "Проверка базовых правил на данных и контрольных суммах",
  HYPOTHESES_EVALUATED: "Сравнение вариантов структуры по данным",
  USER_MAPPING_APPLIED: "Источник поля изменён для всех строк",
  LLM_CANDIDATE_SELECTED: "Модель выбрала проверенный вариант",
  STRUCTURE_UNRESOLVED: "Структура требует уточнения",
  LLM_STRUCTURE_RECOVERY_STARTED: "Резервное распознавание структуры через LLM",
  LLM_STRUCTURE_CHECKED: "Проверка структуры, предложенной LLM",
  LLM_STRUCTURE_ACCEPTED: "Предложенная LLM структура принята",
  LLM_STRUCTURE_REJECTED: "Предложенная LLM структура отклонена",
  LLM_REQUEST: "Запрос к LLM",
  LLM_RESPONSE: "Ответ LLM",
  LLM_VALIDATION_FAILED: "Ответ LLM не прошёл проверку",
  LLM_FAILED: "Ошибка обращения к LLM",
  LLM_SPEC_ACCEPTED: "Спецификация LLM принята",
  LLM_FALLBACK: "Переход на детерминированные правила",
  LLM_PROFILE_FALLBACK: "Переход на резервную модель",
  LLM_SKIPPED: "LLM не использовалась",
  LLM_RESULT_FALLBACK: "Результат LLM заменён базовыми правилами",
  FINAL_SPEC_APPLIED: "Итоговые правила преобразования",
  PARSING_COMPLETED: "Преобразование строк завершено",
  VALIDATION_COMPLETED: "Контроль результата завершён",
  RESULT_EXPORTED: "Выходной файл сформирован",
  PROCESSING_COMPLETED: "Обработка завершена",
  PROCESSING_FAILED: "Обработка завершилась ошибкой",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function columnName(value: unknown) {
  if (typeof value !== "number" || value < 1) return "не сопоставлена";
  let column = value;
  let result = "";
  while (column > 0) {
    column -= 1;
    result = String.fromCharCode(65 + column % 26) + result;
    column = Math.floor(column / 26);
  }
  return `колонка ${value} (${result})`;
}

function describeSpec(details: Record<string, unknown>) {
  const spec = isRecord(details.spec) ? details.spec : details;
  const lines = [
    `Лист: ${String(spec.sheet_name ?? "—")}`,
    `Строки шапки: ${String(spec.header_start_row ?? spec.header_row ?? "—")}–${String(spec.header_end_row ?? spec.header_row ?? "—")}`,
    `Диапазон данных: ${String(spec.data_start_row ?? "—")}–${String(spec.data_end_row ?? "—")}`,
    `Уверенность: ${typeof spec.confidence === "number" ? Math.round(spec.confidence * 100) + "%" : "—"}`,
    "",
    "Сопоставление полей:",
  ];
  const targets = Array.isArray(spec.target_columns) ? spec.target_columns.filter(isRecord) : [];
  const mapping = isRecord(spec.column_mapping) ? spec.column_mapping : {};
  for (const [key, value] of Object.entries(mapping)) {
    const target = targets.find((item) => item.key === key);
    const derived = isRecord(spec.row_rules) && spec.row_rules.account_tree &&
      ["parent_account", "account_level", "row_type"].includes(key);
    lines.push(`- ${String(target?.label ?? key)} [${key}] ← ${derived ? "вычисляется по структуре счетов" : columnName(value)}`);
  }
  if (isRecord(spec.hierarchy) && Array.isArray(spec.hierarchy.levels)) {
    const levels = spec.hierarchy.levels.filter(isRecord);
    lines.push(
      "",
      `Иерархическая колонка: ${columnName(spec.hierarchy.source_column)}`,
      `Уровни аналитики: ${levels.map((level) => String(level.label ?? level.key ?? "—")).join(" → ")}`,
    );
  }
  if (isRecord(spec.row_rules)) {
    const rules = spec.row_rules;
    lines.push("", "Правила строк:");
    lines.push(`- пропускать пустые: ${rules.skip_empty === false ? "нет" : "да"}`);
    if (Array.isArray(rules.skip_keywords)) lines.push(`- пропускать итоговые строки: ${rules.skip_keywords.map(String).join(", ") || "нет"}`);
    if (Array.isArray(rules.inherit_fields)) lines.push(`- наследовать значения: ${rules.inherit_fields.map(String).join(", ") || "нет"}`);
    if (rules.account_tree) lines.push("- контрольные суммы: по верхнему уровню счетов, без повторного сложения субсчетов");
  }
  if (Array.isArray(spec.value_overrides) && spec.value_overrides.length) {
    lines.push("", "Пользовательские переопределения:", JSON.stringify(spec.value_overrides, null, 2));
  }
  if (Array.isArray(spec.notes) && spec.notes.length) {
    lines.push("", "Примечания:", ...spec.notes.map((note) => `- ${String(note)}`));
  }
  return lines.join("\n");
}

function describeSamples(details: Record<string, unknown>) {
  if (!Array.isArray(details.sheets)) return JSON.stringify(details, null, 2);
  const lines: string[] = [];
  for (const rawSheet of details.sheets) {
    if (!isRecord(rawSheet)) continue;
    lines.push(`Лист: ${String(rawSheet.name ?? "—")}`);
    if (!Array.isArray(rawSheet.rows)) continue;
    for (const rawRow of rawSheet.rows) {
      if (!isRecord(rawRow)) continue;
      const values = Array.isArray(rawRow.values) ? rawRow.values : [];
      lines.push(`${String(rawRow.row ?? "—")} | ${values.map((value) => value == null ? "" : String(value)).join(" | ")}`);
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd();
}

function describeEvent(event: TransformationRunEvent) {
  if (event.event_type === "SOURCE_SAMPLE") return describeSamples(event.details);
  if (["BASELINE_SPEC_CREATED", "FINAL_SPEC_APPLIED", "LLM_SPEC_ACCEPTED"].includes(event.event_type)) {
    return describeSpec(event.details);
  }
  return JSON.stringify(event.details, null, 2);
}

function formatMemo(memo: TransformationMemo) {
  const started = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(memo.run.started_at));
  const lines = [
    "ТЕХНИЧЕСКОЕ МЕМО ОБРАБОТКИ",
    `Запуск: ${memo.run.id}`,
    `Целевая версия: ${memo.run.target_version}`,
    `Статус: ${memo.run.status}`,
    `Начало: ${started}`,
  ];
  if (memo.run.feedback) lines.push(`Замечание пользователя: ${memo.run.feedback}`);
  for (const event of memo.events) {
    const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(event.created_at));
    lines.push(
      "",
      "=".repeat(72),
      `[${time}] ${eventLabels[event.event_type] ?? event.event_type} · ${event.stage} · ${event.level}`,
      describeEvent(event),
    );
  }
  return lines.join("\n");
}

export function TechnicalMemo({ transformationId, status }: { transformationId: string; status: TransformationStatus }) {
  const toast = useToast();
  const memo = useQuery({
    queryKey: ["transformation-memo", transformationId],
    queryFn: () => api<TransformationMemo>(`/transformations/${transformationId}/memo`),
    refetchInterval: (query) => {
      const runStatus = query.state.data?.run.status;
      return runStatus === "QUEUED" || runStatus === "RUNNING" || status === "QUEUED" || status === "RUNNING" ? 1000 : false;
    },
    retry: (count, error) => !(error instanceof ApiError && error.code === "MEMO_NOT_AVAILABLE") && count < 2,
  });
  const text = useMemo(() => memo.data ? formatMemo(memo.data) : "", [memo.data]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.show("Техническое мемо скопировано");
    } catch {
      toast.show("Не удалось скопировать мемо", "error");
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob(["\ufeff", text], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `transformation-${transformationId.slice(0, 8)}-memo.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="Техническое мемо обработки"
        description="Файл, исходные строки, правила, запрос и ответ LLM, преобразование и контроль"
        action={memo.data && <div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => void copy()}><Clipboard className="h-3.5 w-3.5" />Копировать</Button><Button size="sm" variant="secondary" onClick={download}><Download className="h-3.5 w-3.5" />TXT</Button></div>}
      />
      {memo.isLoading ? <SkeletonRows count={5} /> : memo.isError ? (
        memo.error instanceof ApiError && memo.error.code === "MEMO_NOT_AVAILABLE"
          ? <div className="flex flex-col items-center px-6 py-12 text-center"><ScrollText className="mb-3 h-7 w-7 text-slate-400" /><p className="font-semibold text-navy">Техническое мемо отсутствует</p><p className="mt-1 text-sm text-slate-500">Логи для старых преобразований не восстанавливаются.</p></div>
          : <div className="p-5"><ErrorState message={memo.error.message} retry={() => void memo.refetch()} /></div>
      ) : (
        <pre className="max-h-[42rem] overflow-auto whitespace-pre-wrap break-words bg-slate-950 p-5 font-mono text-xs leading-5 text-slate-200">{text}</pre>
      )}
    </Card>
  );
}
