/**
 * Single point of contact with the FinTool backend.
 *
 * No component should call `fetch` directly — every backend request goes
 * through one of the functions below, and every one of them uses
 * `API_BASE_URL`, which is read once from the environment. This is the only
 * file that needs to change if the backend's routes or base URL move.
 */
import type {
  ChatRequest,
  ChatResponse,
  ErrorResponse,
  ExplainResponse,
  MetricsResponse,
  RatiosResponse,
  RisksResponse,
  StatusResponse,
  UploadResponse,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string | undefined;

if (!API_BASE_URL) {
  // Fails loudly at startup rather than producing confusing network errors
  // on the first button click.
  throw new Error(
    "VITE_API_BASE_URL is not set. Copy .env.example to .env and set it to " +
      "your running backend's URL (e.g. http://localhost:8000).",
  );
}

/** Thrown for any non-2xx backend response. Carries the parsed error body when available. */
export class ApiError extends Error {
  status: number;
  errorCode: string | null;
  docId: string | null;

  constructor(status: number, body: Partial<ErrorResponse> | null) {
    super(body?.detail ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = body?.error_code ?? null;
    this.docId = body?.doc_id ?? null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);

  if (!response.ok) {
    let body: Partial<ErrorResponse> | null = null;
    try {
      body = await response.json();
    } catch {
      // Response wasn't JSON (e.g. a network-level error page) — fall back
      // to a generic message via ApiError's default.
    }
    throw new ApiError(response.status, body);
  }

  return (await response.json()) as T;
}

// --- Upload ------------------------------------------------------------------

export function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return request<UploadResponse>("/upload", {
    method: "POST",
    body: formData,
  });
}

// --- Status --------------------------------------------------------------

export function getStatus(docId: string): Promise<StatusResponse> {
  return request<StatusResponse>(`/status/${docId}`);
}

// --- Metrics -------------------------------------------------------------

export function getMetrics(docId: string): Promise<MetricsResponse> {
  return request<MetricsResponse>(`/metrics/${docId}`);
}

// --- Ratios --------------------------------------------------------------

export function getRatios(docId: string): Promise<RatiosResponse> {
  return request<RatiosResponse>(`/ratios/${docId}`);
}

// --- Risks -----------------------------------------------------------------

export function getRisks(docId: string): Promise<RisksResponse> {
  return request<RisksResponse>(`/risks/${docId}`);
}

// --- Chat ------------------------------------------------------------------

export function postChat(body: ChatRequest): Promise<ChatResponse> {
  return request<ChatResponse>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// --- Explain -------------------------------------------------------------

export function getExplain(
  docId: string,
  metricName: string,
  year?: number,
): Promise<ExplainResponse> {
  const query = year !== undefined ? `?year=${year}` : "";
  return request<ExplainResponse>(`/explain/${docId}/${metricName}${query}`);
}
