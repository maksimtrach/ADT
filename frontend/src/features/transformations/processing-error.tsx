import { AlertTriangle } from "lucide-react";
import type { ProcessingStage, TransformationErrorDetails } from "../../lib/types";

const stageLabels: Partial<Record<ProcessingStage, string>> = {
  QUEUED: "Подготовка",
  PROFILING: "Анализ файла",
  PLANNING: "Определение структуры",
  TRANSFORMING: "Преобразование данных",
  VALIDATING: "Проверка результата",
  EXPORTING: "Формирование Excel",
};

interface ProcessingErrorProps {
  error: TransformationErrorDetails | null | undefined;
  fallback?: string;
}

export function ProcessingError({ error, fallback }: ProcessingErrorProps) {
  if (!error && !fallback) return null;
  const reason = error?.reason || fallback;
  return (
    <section className="mt-5 border border-red-200 bg-red-50 p-4 text-sm text-red-900" aria-labelledby="processing-error-title">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 id="processing-error-title" className="font-semibold">Причина ошибки</h3>
            {error?.code && <code className="text-xs text-red-700">{error.code}</code>}
          </div>
          {error?.stage && <p className="mt-1 text-xs text-red-700">Этап: {stageLabels[error.stage] || error.stage}</p>}
          <p className="mt-2 font-medium">{reason}</p>
          {Boolean(error?.details.length) && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-red-800">
              {error?.details.map((detail) => <li key={detail}>{detail}</li>)}
            </ul>
          )}
          {error?.recommendation && (
            <div className="mt-3 border-t border-red-200 pt-3">
              <span className="font-semibold">Что можно сделать: </span>{error.recommendation}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
