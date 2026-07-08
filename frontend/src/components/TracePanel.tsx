import { useState } from "react";
import type { AskResult, AttemptTrace, CriticVerdict } from "../types/trace";

/**
 * The reasoning-trace panel — a deliberate design choice, not a debug
 * leftover (see the project README). Collapsed by default; once expanded it
 * must stand on its own: a reviewer asking "show me what the agent actually
 * did" should get the full plan, every SQL attempt, the critic's verdict and
 * hint, and the final answer without any verbal narration.
 */
export function TracePanel({ result }: { result: AskResult }): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const { trace } = result;

  return (
    <div className="trace-panel">
      <button
        type="button"
        className="trace-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        {expanded ? "▾" : "▸"} Reasoning trace — {trace.attempts.length}{" "}
        {trace.attempts.length === 1 ? "attempt" : "attempts"}, {trace.llm_calls_used}/
        {trace.llm_call_budget} LLM calls
      </button>

      {expanded && (
        <div className="trace-body">
          {trace.planner && (
            <section className="trace-section">
              <h4>Plan</h4>
              <ol>
                {trace.planner.steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
              {trace.planner.tables.length > 0 && (
                <p className="trace-meta">
                  <strong>Tables:</strong> {trace.planner.tables.join(", ")}
                </p>
              )}
              <p className="trace-meta">
                <strong>Retrieval query:</strong> "{trace.planner.retrieval_query}"
              </p>
            </section>
          )}

          {trace.attempts.map((attempt) => (
            <AttemptSection key={attempt.attempt} attempt={attempt} />
          ))}

          <section className="trace-section trace-final">
            <h4>Final output</h4>
            {result.status === "ok" ? (
              <>
                <p>{result.answer}</p>
                {result.caveats.length > 0 && (
                  <ul className="trace-caveats">
                    {result.caveats.map((caveat, i) => (
                      <li key={i}>{caveat}</li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="trace-failure">{result.failure_reason}</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function AttemptSection({ attempt }: { attempt: AttemptTrace }): React.JSX.Element {
  return (
    <section className="trace-section">
      <h4>Attempt {attempt.attempt}</h4>

      {attempt.retrieved.length > 0 && (
        <p className="trace-meta">
          <strong>Retrieved:</strong>{" "}
          {attempt.retrieved.map((chunk) => `${chunk.chunk_id} (${chunk.score.toFixed(2)})`).join(", ")}
        </p>
      )}

      {attempt.failure && <p className="trace-failure">{attempt.failure}</p>}

      {attempt.sql && (
        <>
          <p className="trace-meta">
            <strong>SQL</strong>
          </p>
          <pre className="trace-sql">{attempt.sql}</pre>
          {attempt.rationale && <p className="trace-rationale">{attempt.rationale}</p>}
        </>
      )}

      {attempt.execution && (
        <p className="trace-meta">
          <strong>Execution:</strong>{" "}
          {attempt.execution.success ? (
            <>
              {attempt.execution.row_count} row{attempt.execution.row_count === 1 ? "" : "s"}
              {attempt.execution.truncated ? " (truncated)" : ""} in {attempt.execution.duration_ms}
              ms
            </>
          ) : (
            <span className="trace-failure">{attempt.execution.error}</span>
          )}
        </p>
      )}

      {attempt.critic && (
        <p className="trace-meta">
          <strong>Critic:</strong> <VerdictBadge verdict={attempt.critic.verdict} />
          {attempt.critic.issues.length > 0 && <> — {attempt.critic.issues.join("; ")}</>}
          {attempt.critic.hint && <> (hint: {attempt.critic.hint})</>}
        </p>
      )}
    </section>
  );
}

function VerdictBadge({ verdict }: { verdict: CriticVerdict }): React.JSX.Element {
  return <span className={`verdict-badge verdict-${verdict}`}>{verdict}</span>;
}
