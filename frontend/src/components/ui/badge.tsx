import { cn } from "../../lib/cn";

const tones: Record<string, string> = {
  COMPLETED: "bg-emerald-50 text-emerald-700",
  PASSED: "bg-emerald-50 text-emerald-700",
  RUNNING: "bg-blue-50 text-blue-700",
  QUEUED: "bg-slate-100 text-slate-700",
  WARNINGS: "bg-amber-50 text-amber-800",
  FAILED: "bg-red-50 text-red-700",
  NOT_PERFORMED: "bg-slate-100 text-slate-600",
  SUCCESS: "bg-emerald-50 text-emerald-700",
  FAILURE: "bg-red-50 text-red-700",
};

const labels: Record<string, string> = {
  COMPLETED: "Завершено", RUNNING: "Выполняется", QUEUED: "В очереди", FAILED: "Ошибка",
  PASSED: "Пройден", WARNINGS: "С замечаниями", NOT_PERFORMED: "Не выполнялся",
  SUCCESS: "Успешно", FAILURE: "Ошибка",
};

export function Badge({ value, className }: { value: string; className?: string }) {
  return <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm px-2 py-1 text-xs font-semibold", tones[value] ?? "bg-slate-100 text-slate-700", className)}><span className="h-1.5 w-1.5 rounded-full bg-current" />{labels[value] ?? value}</span>;
}

