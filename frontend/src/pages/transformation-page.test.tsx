// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TransformationDetail } from "../lib/types";
import { TransformationPage } from "./transformation-page";

const { request, showToast } = vi.hoisted(() => ({ request: vi.fn(), showToast: vi.fn() }));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()), api: request, downloadResult: vi.fn(),
}));
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

async function mount(path = "/transformations/test") {
  await act(async () => {
    root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/transformations" element={<TransformationPage />} /><Route path="/transformations/:transformationId" element={<TransformationPage />} /></Routes>
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
    if (path === "/transformations/document-profiles") return [];
    if (path.endsWith("/retry")) return retryRequest();
    if (path.endsWith("/cancel")) {
      detail = { ...detail, status: "CANCELLED", current_stage: "CANCELLED", error_message: undefined };
      return detail;
    }
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

describe("Новое преобразование", () => {
  it("прерывает активную обработку перед очисткой формы", async () => {
    detail = { ...detail, status: "RUNNING", current_stage: "PLANNING", error_message: undefined };
    await mount();
    expect(button("Новое преобразование")).toBeDefined();
    await act(async () => button("Новое преобразование").click());
    await settle();
    expect(request).toHaveBeenCalledWith("/transformations/test/cancel", { method: "POST" });
    expect(showToast).toHaveBeenCalledWith("Преобразование прервано");
    expect(textarea().disabled).toBe(true);
    expect(container.textContent).toContain("Обработка не запущена");
  });

  it("очищает завершённое состояние без запроса отмены", async () => {
    await mount();
    await enterComment("Черновик");
    await act(async () => button("Новое преобразование").click());
    await settle();
    expect(request.mock.calls.some(([path]) => path.endsWith("/cancel"))).toBe(false);
    expect(textarea().value).toBe("");
    expect(textarea().disabled).toBe(true);
  });
});

describe("Повторная обработка", () => {
  it("доступна после ошибки без успешной версии; результат не показывается", async () => {
    await mount();
    expect(textarea()).not.toBeNull();
    expect(button("Повторить без изменений").disabled).toBe(false);
    expect(button("Повторить с комментарием").disabled).toBe(true);
    expect(container.textContent).toContain("Загружать файл заново не требуется");
    expect(container.textContent).toContain("Структура неоднозначна");
    expect(container.textContent).not.toContain("Выгрузить в Excel");
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
    expect(button("Выгрузить в Excel")).toBeDefined();
    expect(button("Повторить без изменений").disabled).toBe(false);
    detail = { ...detail, status: "FAILED", current_stage: "FAILED" };
    await act(async () => { client.setQueryData(["transformation", "test"], detail); });
    await settle();
    expect(container.textContent).not.toContain("Выгрузить в Excel");
    expect(textarea()).not.toBeNull();
  });
});

describe("Вертикальная компоновка", () => {
  const blocks = [
    "1. Загрузка файла", "2. Параметры преобразования", "3. Статус преобразования",
    "4. Результаты преобразования", "5. Корректировки",
  ];

  it("показывает все пять блоков по порядку до загрузки", async () => {
    await mount("/transformations");
    expect(Array.from(container.querySelectorAll("h2"), (heading) => heading.textContent)).toEqual(blocks);
    expect(textarea().disabled).toBe(true);
    expect(button("Преобразовать").disabled).toBe(true);
    expect(button("Повторить без изменений").disabled).toBe(true);
    expect(container.textContent).toContain("Результат пока не готов");
  });

  it("показывает первые строки в области с прокруткой и кнопку выгрузки после успеха", async () => {
    detail = { ...detail, status: "COMPLETED", current_stage: "COMPLETED", version: 1, error_message: undefined };
    request.mockImplementation(async (path: string) => {
      if (path === "/transformations/templates") return [];
      if (path === "/transformations/test") return detail;
      if (path.endsWith("/preview")) return {
        columns: [
          { key: "account", label: "Счет", kind: "text", required: true },
          { key: "parent_account", label: "Родительский счёт", kind: "text", required: false },
          { key: "account_level", label: "Уровень счёта", kind: "text", required: false },
          { key: "row_type", label: "Тип строки", kind: "text", required: false },
          { key: "parent_source_row", label: "Строка родительского счёта", kind: "text", required: false },
        ],
        rows: [{ account: "01", parent_account: null, account_level: "1", row_type: "Детальный счёт", parent_source_row: null, _source_row: 8 }], total: 120, version: 1,
      };
      throw new Error(`Неожиданный запрос: ${path}`);
    });
    await mount();
    expect(Array.from(container.querySelectorAll("h2"), (heading) => heading.textContent)).toEqual(blocks);
    expect(button("Выгрузить в Excel")).toBeDefined();
    const region = container.querySelector<HTMLElement>('[role="region"][aria-label="Первые строки результата"]');
    expect(region?.className).toContain("overflow-auto");
    expect(region?.className).toContain("max-h-[32rem]");
    expect(region?.querySelector("th")?.className).toContain("sticky");
    expect(region?.textContent).toContain("01");
    expect(region?.textContent).toContain("Родительский счёт");
    expect(region?.textContent).toContain("Уровень счёта");
    expect(region?.textContent).toContain("Тип строки");
    expect(region?.textContent).toContain("Строка родительского счёта");
    expect(region?.textContent).toContain("Детальный счёт");
    expect(region?.querySelector("th:nth-child(2)")?.className).toContain("border-l-2");
    expect(container.textContent).toContain("Показаны первые 1 из 120 записей");
  });
});

describe("Предпросмотр исходного файла", () => {
  it("показывает 100 строк, переключает лист и запускает обработку без повторной передачи файла", async () => {
    const rows = Array.from({ length: 100 }, (_, index) => ({ row: index + 1, cells: [index + 1, index === 0 ? "Заголовок" : null] }));
    const source = {
      upload_id: "staged-1", filename: "source.xlsx", sheets: ["ОСВ", "Второй лист"],
      sheet_name: "ОСВ", columns: ["A", "B"], rows, total_rows: 105,
      total_columns: 2, columns_truncated: false,
    };
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/transformations/templates") return [];
      if (path === "/transformations/source-uploads" && init?.method === "POST") return source;
      if (path.includes("/source-uploads/staged-1/preview?sheet=")) {
        return { ...source, sheet_name: "Второй лист", columns: ["A"], rows: [{ row: 1, cells: ["Другой лист"] }] };
      }
      if (path === "/transformations" && init?.method === "POST") {
        return { ...detail, id: "created", status: "QUEUED", current_stage: "QUEUED" };
      }
      if (path === "/transformations/created") return { ...detail, id: "created", status: "QUEUED", current_stage: "QUEUED" };
      throw new Error(`Неожиданный запрос: ${path}`);
    });
    await mount("/transformations");
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await act(async () => {
      Object.defineProperty(input, "files", { configurable: true, value: [new File(["xlsx"], "source.xlsx")] });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    const region = container.querySelector<HTMLElement>('[role="region"][aria-label="Предпросмотр исходного файла"]');
    expect(region?.className).toContain("max-h-[34rem]");
    expect(region?.className).toContain("overflow-auto");
    expect(region?.querySelectorAll("tbody tr")).toHaveLength(100);
    expect(region?.querySelector("thead th")?.className).toContain("sticky");
    expect(container.textContent).toContain("первые 100 из 105 строк");
    expect(button("Преобразовать").disabled).toBe(false);

    const sheet = container.querySelector<HTMLSelectElement>("#source-sheet")!;
    await act(async () => { sheet.value = "Второй лист"; sheet.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
    expect(container.querySelector('[aria-label="Предпросмотр исходного файла"]')?.textContent).toContain("Другой лист");

    await act(async () => button("Преобразовать").click());
    const create = request.mock.calls.find(([path, init]) => path === "/transformations" && init?.method === "POST");
    const body = create?.[1]?.body as FormData;
    expect(body.get("upload_id")).toBe("staged-1");
    expect(body.get("file")).toBeNull();
    await settle();
    expect(container.querySelector('[aria-label="Предпросмотр исходного файла"]')?.textContent).toContain("Другой лист");
    expect(container.textContent).toContain("source.xlsx");
    expect(button("Преобразовать").disabled).toBe(true);
    expect(container.querySelector<HTMLSelectElement>("#source-sheet")?.disabled).toBe(true);
  });

  it("оставляет преобразование недоступным, если предварительное чтение файла завершилось ошибкой", async () => {
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/transformations/templates") return [];
      if (path === "/transformations/source-uploads" && init?.method === "POST") throw new Error("Файл повреждён");
      throw new Error(`Неожиданный запрос: ${path}`);
    });
    await mount("/transformations");
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await act(async () => {
      Object.defineProperty(input, "files", { configurable: true, value: [new File(["bad"], "source.xlsx")] });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(container.textContent).toContain("Файл повреждён");
    expect(button("Преобразовать").disabled).toBe(true);
    expect(button("Повторить загрузку")).toBeDefined();
  });
});
