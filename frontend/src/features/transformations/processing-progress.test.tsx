// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { TransformationDetail } from "../../lib/types";
import { ProcessingProgress } from "./processing-progress";

afterEach(() => vi.useRealTimers());

it("counts seconds, resets on retry, and freezes on failure", async () => {
  vi.useFakeTimers();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div");
  const root = createRoot(container);
  const detail = { status: "RUNNING", processing_run_id: "first", elapsed_seconds: 12,
    current_operation: "распознавание шапки через LLM" } as TransformationDetail;
  try {
    await act(async () => root.render(<ProcessingProgress detail={detail} />));
    expect(container.textContent).toContain("(12 с · распознавание шапки через LLM)");
    await act(async () => vi.advanceTimersByTime(2000));
    expect(container.textContent).toContain("(14 с");
    await act(async () => root.render(<ProcessingProgress detail={{ ...detail, elapsed_seconds: 14,
      current_operation: "определение шапки и колонок · проверка гипотезы 3 из 7" }} />));
    expect(container.textContent).toContain("(14 с · определение шапки и колонок · проверка гипотезы 3 из 7)");
    await act(async () => root.render(<ProcessingProgress detail={{ ...detail, processing_run_id: "second", elapsed_seconds: 0 }} />));
    expect(container.textContent).toContain("(0 с");
    await act(async () => root.render(<ProcessingProgress detail={{ ...detail, status: "FAILED", elapsed_seconds: 38 }} />));
    await act(async () => vi.advanceTimersByTime(5000));
    expect(container.textContent).toContain("(38 с");
  } finally {
    await act(async () => root.unmount());
  }
});
