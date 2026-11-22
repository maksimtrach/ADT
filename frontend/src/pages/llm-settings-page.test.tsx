// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LLMSettingsPage } from "./llm-settings-page";
import type { LLMSettingsResponse } from "../lib/types";

const { request, notify } = vi.hoisted(() => ({ request: vi.fn(), notify: vi.fn() }));
vi.mock("../lib/api", () => ({ api: request }));
vi.mock("../components/ui/toast", () => ({ useToast: () => ({ show: notify }) }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

const response: LLMSettingsResponse = {
  selected_profile: "local",
  environment_override: null,
  configuration: {
    active_profile: "local",
    fallback_profiles: [],
    profiles: {
      local: {
        enabled: true,
        protocol: "ollama",
        base_url: "http://127.0.0.1:11434",
        model: "qwen/old",
        timeout_seconds: 180,
        connect_timeout_seconds: 10,
        thinking: false,
        no_think_suffix: true,
        keep_alive: "10m",
        api_key_env: null,
        structured_output: true,
        generation: { temperature: 0, max_tokens: 1800, context_size: 16384 },
      },
    },
  },
};

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}

function button(label: string) {
  return Array.from(container.querySelectorAll("button")).find((item) => item.textContent?.includes(label));
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/llm-settings" && !init) return structuredClone(response);
    if (path === "/llm-settings/test") return {
      profile_name: "local", server_available: true, model_available: true, available: true,
      latency_ms: 25, status_code: 200, message: "Модель доступна", models: ["qwen/new"],
    };
    if (path === "/llm-settings" && init?.method === "PUT") {
      return { ...response, configuration: JSON.parse(init.body as string) };
    }
    throw new Error(`Unexpected request: ${path}`);
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
});

it("проверяет несохранённый профиль и сохраняет изменённую модель", async () => {
  await act(async () => root.render(<QueryClientProvider client={client}><LLMSettingsPage /></QueryClientProvider>));
  await settle();

  const modelInput = Array.from(container.querySelectorAll("input")).find((input) => input.value === "qwen/old")!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(modelInput, "qwen/new");
    modelInput.dispatchEvent(new Event("input", { bubbles: true }));
  });

  await act(async () => button("Проверить")!.click());
  await settle();
  const probeCall = request.mock.calls.find(([path]) => path === "/llm-settings/test");
  expect(JSON.parse(probeCall![1].body).profile.model).toBe("qwen/new");
  expect(container.textContent).toContain("Модель доступна");

  await act(async () => button("Сохранить настройки")!.click());
  await settle();
  const saveCall = request.mock.calls.find(([path, init]) => path === "/llm-settings" && init?.method === "PUT");
  expect(JSON.parse(saveCall![1].body).profiles.local.model).toBe("qwen/new");
  expect(notify).toHaveBeenCalledWith("Настройки LLM сохранены");
});
