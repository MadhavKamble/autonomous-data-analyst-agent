import type {
  AskResponse,
  HealthResponse,
  MessageOut,
  SessionSummary,
} from "../types/trace";

/** Vite env var, falls back to the local dev backend. Set
 * VITE_API_BASE_URL at build time to point at the deployed Render service. */
const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number | undefined;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (error) {
    throw new ApiError(
      `Could not reach the agent service: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  if (!response.ok) {
    // FastAPI's HTTPException body is {"detail": "..."}; fall back to
    // status text if the body isn't JSON (e.g. a proxy error page).
    const detail = await response
      .json()
      .then((body: unknown) =>
        typeof body === "object" && body !== null && "detail" in body
          ? String((body as { detail: unknown }).detail)
          : response.statusText,
      )
      .catch(() => response.statusText);
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface HealthProbeResult {
  reachable: boolean;
  healthy: boolean;
  durationMs: number;
}

/** Hits /health and reports both reachability and timing — the timing is
 * what lets the UI distinguish "slow but alive" (cold start in progress)
 * from "answered instantly" without waiting for a hard timeout. */
export async function probeHealth(timeoutMs: number): Promise<HealthProbeResult> {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}/health`, { signal: controller.signal });
    const durationMs = performance.now() - started;
    if (!response.ok) {
      return { reachable: true, healthy: false, durationMs };
    }
    const body = (await response.json()) as HealthResponse;
    return { reachable: true, healthy: body.status === "ok", durationMs };
  } catch {
    return { reachable: false, healthy: false, durationMs: performance.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

export function askQuestion(question: string, sessionId?: string): Promise<AskResponse> {
  return request<AskResponse>("/ask", {
    method: "POST",
    body: JSON.stringify(sessionId ? { question, session_id: sessionId } : { question }),
  });
}

export function listSessions(): Promise<SessionSummary[]> {
  return request<SessionSummary[]>("/sessions");
}

export function listMessages(sessionId: string): Promise<MessageOut[]> {
  return request<MessageOut[]>(`/sessions/${sessionId}/messages`);
}

export function deleteSession(sessionId: string): Promise<void> {
  return request<void>(`/sessions/${sessionId}`, { method: "DELETE" });
}
