import { useState } from "react";
import { ApiError, postChat } from "../api/client";
import type { ChatResponse, ConversationTurn } from "../api/types";

interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  sources?: ChatResponse["sources"];
  warning?: string | null;
}

interface ChatPanelProps {
  docId: string;
}

/** Step 5: POST /chat, keeping a simple running conversation. */
export function ChatPanel({ docId }: ChatPanelProps) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || isSending) return;

    const history: ConversationTurn[] = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setQuestion("");
    setIsSending(true);
    setError(null);

    try {
      const response = await postChat({ doc_id: docId, question: trimmed, conversation_history: history });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.answer, sources: response.sources, warning: response.warning },
      ]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Chat request failed.");
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="panel">
      <h2>Ask about this document</h2>
      <div className="chat-history">
        {messages.map((m, i) => (
          <div key={i} className={`chat-message chat-${m.role}`}>
            <p><strong>{m.role === "user" ? "You" : "Assistant"}:</strong> {m.content}</p>
            {m.sources && m.sources.length > 0 && (
              <ul className="chat-sources">
                {m.sources.map((s, j) => (
                  <li key={j}>
                    Page {s.page_no}{s.section_type ? ` (${s.section_type})` : ""}
                    {s.snippet ? `: "${s.snippet}"` : ""}
                  </li>
                ))}
              </ul>
            )}
            {m.warning && <p className="muted">Warning: {m.warning}</p>}
          </div>
        ))}
        {messages.length === 0 && <p className="muted">No messages yet — ask a question below.</p>}
      </div>

      <form onSubmit={handleSubmit} className="chat-input-row">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. What was the operating cash flow in 2024?"
          disabled={isSending}
        />
        <button type="submit" disabled={!question.trim() || isSending}>
          {isSending ? "Asking…" : "Ask"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
