import { useEffect, useRef, useState } from "react";
import { ApiError, askQuestion, listMessages } from "../api/client";
import { useColdStartGuard } from "../hooks/useColdStartGuard";
import type { MessageOut } from "../types/trace";
import { ColdStartBanner } from "./ColdStartBanner";
import { MessageBubble, type DisplayMessage } from "./MessageBubble";

const SESSION_STORAGE_KEY = "adaa_session_id";

function toDisplayMessage(message: MessageOut, key: string): DisplayMessage {
  return {
    key,
    role: message.role,
    content: message.content,
    trace: message.trace,
    pending: false,
  };
}

export function Chat(): React.JSX.Element {
  const { status: healthStatus, markWarm } = useColdStartGuard();
  const [sessionId, setSessionId] = useState<string | undefined>(
    () => localStorage.getItem(SESSION_STORAGE_KEY) ?? undefined,
  );
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Reconstruct history from Postgres on load — proves conversation state
  // never depended on this browser tab's (or the backend process's) memory.
  useEffect(() => {
    if (!sessionId) return;
    listMessages(sessionId)
      .then((history) => {
        setMessages(history.map((m) => toDisplayMessage(m, String(m.id))));
      })
      .catch((error: unknown) => {
        // A stale/deleted session id shouldn't brick the app — start fresh.
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(undefined);
        setLoadError(error instanceof ApiError ? error.message : "Could not load session history.");
      });
    // Deliberately runs once per mount only — sessionId changes after this
    // are driven by handleSubmit, which already has the messages it needs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSubmit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    const question = input.trim();
    if (!question || sending) return;

    setInput("");
    setSending(true);
    setLoadError(null);

    const userKey = `local-${Date.now()}-u`;
    const pendingKey = `local-${Date.now()}-a`;
    setMessages((current) => [
      ...current,
      { key: userKey, role: "user", content: question, trace: null, pending: false },
      { key: pendingKey, role: "assistant", content: "", trace: null, pending: true },
    ]);

    try {
      const response = await askQuestion(question, sessionId);
      markWarm();
      if (!sessionId) {
        setSessionId(response.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, response.session_id);
      }
      const { result } = response;
      const content = result.status === "ok" ? (result.answer ?? "") : (result.failure_reason ?? "");
      setMessages((current) =>
        current.map((m) => (m.key === pendingKey ? { ...m, content, trace: result, pending: false } : m)),
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "The request failed unexpectedly.";
      setMessages((current) =>
        current.map((m) => (m.key === pendingKey ? { ...m, content: message, pending: false } : m)),
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat">
      <ColdStartBanner status={healthStatus} />
      {loadError && <div className="load-error">{loadError}</div>}

      <div className="message-list">
        {messages.length === 0 && (
          <p className="empty-state">
            Ask a question about the ride-sharing data — e.g. "Which zones have the highest
            cancellation rate?"
          </p>
        )}
        {messages.map((message) => (
          <MessageBubble key={message.key} message={message} />
        ))}
        <div ref={bottomRef} />
      </div>

      <form className="composer" onSubmit={(event) => void handleSubmit(event)}>
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask about ride-sharing data…"
          disabled={sending}
        />
        <button type="submit" disabled={sending || input.trim().length === 0}>
          {sending ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
