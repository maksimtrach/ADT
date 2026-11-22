// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DocumentMemoryPage } from "./document-memory-page";
import type { MemoryRecord } from "../lib/types";

const { request, download, notify } = vi.hoisted(() => ({ request: vi.fn(), download: vi.fn(), notify: vi.fn() }));
vi.mock("../lib/api", () => ({ api: request, downloadFile: download }));
vi.mock("../components/ui/toast", () => ({ useToast: () => ({ show: notify }) }));
vi.mock("../features/auth/auth-context", () => ({ useAuth: () => ({ user: { id: "owner", role: "USER" } }) }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let record: MemoryRecord;

async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
function button(label: string) { return Array.from(container.querySelectorAll("button")).find((item) => item.textContent === label); }
async function mount() {
  await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter><DocumentMemoryPage /></MemoryRouter></QueryClientProvider>); });
  await settle();
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  record = { owner_id: "owner", scope: "private", status: "draft", successes: 0, rejections: 0,
    card: { id: "card-id", revision: 1, document_type: "trial-balance-general", template_id: "trial_balance_flat_v1",
      title: "Общая ОСВ", description: "Точное совпадение шапки", header: [["счет", "наименование"]],
      column_mapping: { account: 1, account_name: 2 }, row_mode: "account_tree", verified_rows: 3, independent_totals: false } };
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/document-memory") return [record];
    if (path === "/document-memory/cases" || path === "/transformations/document-profiles") return [];
    if (path.endsWith("/status")) { record = { ...record, ...JSON.parse(init?.body as string) }; return record; }
    if (path === "/document-memory/import") return { imported: 1, skipped: 0 };
    throw new Error(`Unexpected ${path}`);
  });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });

it("показывает доказательства и подтверждает частный подход явным действием", async () => {
  await mount();
  expect(container.textContent).toContain("Черновик");
  expect(container.textContent).toContain("полного подтверждения нет");
  expect(container.textContent).toContain("Шапка и правила сопоставления");
  expect(button("Сделать общим")).toBeUndefined();
  await act(async () => button("Подтвердить подход")!.click());
  await settle();
  const call = request.mock.calls.find(([path]) => path === "/document-memory/card-id/status");
  expect(JSON.parse(call![1].body)).toEqual({ revision: 1, status: "approved", scope: "private" });
  expect(container.textContent).toContain("Подтверждён");
});

it("не даёт обычному пользователю менять чужой общий подход", async () => {
  record = { ...record, owner_id: "someone", scope: "shared", status: "approved" };
  await mount();
  expect(container.textContent).toContain("Общий");
  expect(button("Подтвердить подход")).toBeUndefined();
  expect(button("Отключить")).toBeUndefined();
  await act(async () => button("Экспортировать память")!.click());
  expect(download).toHaveBeenCalledWith("/document-memory/export", "document-memory.json");
});

it("отправляет импортируемый пакет и сообщает о новых черновиках", async () => {
  await mount();
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file = new File(['{"format_version":1}'], "memory.json", { type: "application/json" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await settle();
  const call = request.mock.calls.find(([path]) => path === "/document-memory/import");
  expect((call![1].body as FormData).get("file")).toBe(file);
  expect(notify).toHaveBeenCalledWith("Добавлено черновиков: 1. Уже присутствуют: 0");
});
