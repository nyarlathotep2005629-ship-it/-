import type { ApiErrorBody } from "./types";

const API_BASE = "/api/v1";

export class ApiRequestError extends Error {
  readonly body: ApiErrorBody;

  constructor(body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiRequestError";
    this.body = body;
  }
}

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json()) as ApiErrorBody;
    throw new ApiRequestError(body);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: "GET" }, signal),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }, signal),
  delete: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: "DELETE" }, signal),
};
