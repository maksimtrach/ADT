// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TransformationDetail } from "../lib/types";
import { TransformationPage } from "./transformation-page";

const { request, showToast } = vi.hoisted(() => ({ request: vi.fn(), showToast: vi.fn() }));
vi.mock("../lib/api", () => ({ api: request, downloadResult: vi.fn() }));
vi.mock("../components/ui/toast", () => ({ useToast: () => ({ show: showToast }) }));
vi.mock("../features/transformations/technical-memo", () => ({ TechnicalMemo: () => <div>Мемо сохранено</div> }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let detail: TransformationDetail;
let retryRequest: () => Promise<TransformationDetail>;

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}

function button(label: string) {
  const found = Array.from(container.querySelectorAll("button")).find((item) => item.textContent === label);
  if (!found) throw new Error(`Кнопка не найдена: ${label}`);
  return found;
}

function textarea() { return container.querySelector<HTMLTextAreaElement>("#feedback")!; }

async function enterComment(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea(), value);
    textarea().dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function mount() {
  await act(async () => {
    root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/transformations/test"]}>
      <Routes><Route path="/transformations/:transformationId" element={<TransformationPage />} /></Routes>
    </MemoryRouter></QueryClientProvider>);
  });
  await settle();
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  detail = {
    id: "test", source_filename: "source.xlsx", data_type: "TRIAL_BALANCE", template_id: "trial_balance_flat_v1",
    status: "FAILED", current_stage: "FAILED", version: 0, processed_rows: 0, unprocessed_rows: 0,
    control_status: "NOT_PERFORMED", created_at: "2026-10-22", updated_at: "2026-10-22",
    warnings: [], controls: {}, llm_used: false, error_message: "Структура неоднозначна",
  };
  retryRequest = async () => {
    detail = { ...detail, status: "QUEUED", current_stage: "QUEUED", error_message: undefined };
    return detail;
  };
  request.mockImplementation(async (path: string) => {
    if (path === "/transformations/templates") return [];
    if (path.endsWith("/retry")) return retryRequest();
    if (path.endsWith("/preview")) return { columns: [], rows: [], total: 0, version: detail.version };
    if (path === "/transformations/test") return detail;
    throw new Error(`Неожиданный запрос: ${path}`);
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
});

describe("Повторная обработка", () => {
  it("доступна после ошибки без успешной версии; результат не показывается", async () => {
    await mount();
    expect(textarea()).not.toBeNull();
    expect(button("Повторить без изменений").disabled).toBe(false);
    expect(button("Повторить с комментарием").disabled).toBe(true);
    expect(container.textContent).toContain("Загружать файл заново не требуется");
    expect(container.textContent).toContain("Структура неоднозначна");
    expect(container.textContent).not.toContain("Скачать Excel");
    expect(request.mock.calls.some(([path]) => path.endsWith("/preview"))).toBe(false);
  });

  it("сохраняет комментарий при повторной ошибке и блокирует обе кнопки во время запуска", async () => {
    let resolveRetry!: (value: TransformationDetail) => void;
    retryRequest = () => new Promise((resolve) => { resolveRetry = resolve; });
    await mount();
    await enterComment("Брать счета из колонки C");
    await act(async () => button("Повторить с комментарием").click());
    await settle();
    expect(button("Повторить без изменений").disabled).toBe(true);
    expect(button("Повторить с комментарием").disabled).toBe(true);
    expect(request).toHaveBeenCalledWith("/transformations/test/retry", {
      method: "POST", body: JSON.stringify({ feedback: "Брать счета из колонки C" }),
    });
    detail = { ...detail, status: "QUEUED", current_stage: "QUEUED", error_message: undefined };
    await act(async () => resolveRetry(detail));
    await settle();
    expect(button("Повторить без изменений").disabled).toBe(true);
    detail = { ...detail, status: "FAILED", current_stage: "FAILED", error_message: "Новая ошибка" };
    await act(async () => { client.setQueryData(["transformation", "test"], detail); });
    await settle();
    expect(textarea().value).toBe("Брать счета из колонки C");
    expect(textarea().disabled).toBe(false);
    expect(button("Повторить с комментарием").disabled).toBe(false);
    expect(container.textContent).toContain("Новая ошибка");
  });

  it("повторяет без комментария, не удаляя введённый черновик", async () => {
    await mount();
    await enterComment("Мой черновик");
    await act(async () => button("Повторить без изменений").click());
    await settle();
    expect(request).toHaveBeenCalledWith("/transformations/test/retry", {
      method: "POST", body: JSON.stringify({ feedback: null }),
    });
    expect(textarea().value).toBe("Мой черновик");
  });

  it("сохраняет комментарий и доступность кнопок при отказе API", async () => {
    retryRequest = async () => { throw new Error("Настроенные LLM недоступны"); };
    await mount();
    await enterComment("Изменить правила");
    await act(async () => button("Повторить с комментарием").click());
    await settle();
    expect(textarea().value).toBe("Изменить правила");
    expect(button("Повторить с комментарием").disabled).toBe(false);
    expect(showToast).toHaveBeenCalledWith("Настроенные LLM недоступны", "error");
  });

  it("показывает результат только после успеха, но оставляет форму после последующей ошибки", async () => {
    detail = { ...detail, status: "COMPLETED", current_stage: "COMPLETED", version: 1, error_message: undefined };
    await mount();
    expect(button("Скачать Excel")).toBeDefined();
    expect(button("Повторить без изменений").disabled).toBe(false);
    detail = { ...detail, status: "FAILED", current_stage: "FAILED" };
    await act(async () => { client.setQueryData(["transformation", "test"], detail); });
    await settle();
    expect(container.textContent).not.toContain("Скачать Excel");
    expect(textarea()).not.toBeNull();
  });
});
