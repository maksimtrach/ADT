import { useEffect, useState } from "react";
import type { TransformationDetail } from "../../lib/types";

export function ProcessingProgress({ detail }: { detail: TransformationDetail }) {
  const active = detail.status === "QUEUED" || detail.status === "RUNNING";
  const elapsed = detail.elapsed_seconds ?? 0;
  const [seconds, setSeconds] = useState(Math.floor(elapsed));
  useEffect(() => {
    setSeconds(Math.floor(elapsed));
    if (!active || !detail.processing_run_id) return;
    const anchor = performance.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor(elapsed + (performance.now() - anchor) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active, detail.processing_run_id, elapsed]);
  if (!detail.processing_run_id) return null;
  return <span className="text-xs text-slate-600 tabular-nums" aria-label="Время обработки и текущая операция">
    ({active ? seconds : Math.floor(elapsed)} с · {detail.current_operation || "обработка файла"})
  </span>;
}
