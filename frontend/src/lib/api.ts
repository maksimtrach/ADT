const API_URL = import.meta.env.VITE_API_URL ?? "/api";

export interface PaginationMeta { page: number; pageSize: number; total: number }
interface ApiEnvelope<T> { data: T; meta?: PaginationMeta }

export class ApiError extends Error {
  constructor(public status: number, message: string, public code = "REQUEST_FAILED") { super(message); }
}

async function requestEnvelope<T>(path: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: { message: "Ошибка запроса" } })) as { error?: { code?: string; message?: string } };
    throw new ApiError(response.status, body.error?.message ?? "Ошибка запроса", body.error?.code);
  }
  if (response.status === 204) return { data: undefined as T };
  return await response.json() as ApiEnvelope<T>;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> { return (await requestEnvelope<T>(path, init)).data; }
export async function apiWithMeta<T>(path: string, init?: RequestInit) { return requestEnvelope<T>(path, init); }

export async function downloadResult(id: string, fallbackName: string) {
  const response = await fetch(`${API_URL}/transformations/${id}/download`, { credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: { message: "Не удалось скачать файл" } })) as { error?: { message?: string } };
    throw new ApiError(response.status, body.error?.message ?? "Не удалось скачать файл");
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const name = encoded ? decodeURIComponent(encoded) : fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

