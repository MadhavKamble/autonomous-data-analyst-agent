import type { AskResult, MessageRole } from "../types/trace";
import { TracePanel } from "./TracePanel";

export interface DisplayMessage {
  key: string;
  role: MessageRole;
  content: string;
  trace: AskResult | null;
  pending: boolean;
}

export function MessageBubble({ message }: { message: DisplayMessage }): React.JSX.Element {
  return (
    <div className={`message message-${message.role}`}>
      <div className="message-bubble">
        {message.pending ? <span className="message-pending">Thinking…</span> : message.content}
      </div>
      {message.trace && <TracePanel result={message.trace} />}
    </div>
  );
}
