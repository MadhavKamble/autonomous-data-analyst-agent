/**
 * Mirrors backend/app/orchestrator/trace.py and backend/app/api/schemas.py
 * field-for-field. Keep these two files in sync by hand — there is no
 * codegen step (the backend is small and stable enough that this is the
 * pragmatic choice; the API's actual test coverage is Python-side).
 *
 * JSON wire shape notes:
 * - Python `list[str] = Field(default_factory=list)` always serializes as an
 *   array (never absent), so those fields are NOT optional here.
 * - Python `X | None` serializes as `null`, so those fields are `T | null`
 *   (not `T | undefined`) — pydantic's `model_dump(mode="json")` always
 *   includes the key.
 * - UUIDs and datetimes serialize as strings.
 */

export interface PlannerOutput {
  steps: string[];
  tables: string[];
  retrieval_query: string;
}

export interface RetrievedChunkTrace {
  chunk_id: string;
  kind: string;
  score: number;
  preview: string;
}

export interface ExecutionTrace {
  success: boolean;
  columns: string[];
  sample_rows: unknown[][];
  row_count: number;
  truncated: boolean;
  error: string | null;
  duration_ms: number;
}

export type CriticVerdict = "pass" | "fail";

export interface CriticTrace {
  verdict: CriticVerdict;
  issues: string[];
  hint: string;
}

export interface AttemptTrace {
  attempt: number;
  retrieved: RetrievedChunkTrace[];
  sql: string | null;
  rationale: string;
  execution: ExecutionTrace | null;
  critic: CriticTrace | null;
  failure: string | null;
}

export interface ReasoningTrace {
  planner: PlannerOutput | null;
  attempts: AttemptTrace[];
  llm_calls_used: number;
  llm_call_budget: number;
}

export interface ResultData {
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
}

export type AskStatus = "ok" | "failed";

export interface AskResult {
  status: AskStatus;
  answer: string | null;
  caveats: string[];
  failure_reason: string | null;
  data: ResultData | null;
  trace: ReasoningTrace;
}

// -- HTTP envelope (api/schemas.py) -----------------------------------------

export interface AskRequest {
  question: string;
  session_id?: string;
}

export interface AskResponse {
  session_id: string;
  message_id: number;
  result: AskResult;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  last_active_at: string;
}

export type MessageRole = "user" | "assistant";

export interface MessageOut {
  id: number;
  role: MessageRole;
  content: string;
  trace: AskResult | null;
  created_at: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "unreachable";
}
