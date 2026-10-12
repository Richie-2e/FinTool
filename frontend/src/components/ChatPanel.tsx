import { useEffect, useRef } from "react";
import type { SourceItem } from "../api/types";
import type { ChatState } from "../hooks/useChat";
import { renderMarkdown } from "../utils/markdown";

interface ChatPanelProps {
  chat: ChatState;
}

/** How close to the bottom (px) still counts as "following" the conversation. */
const STICK_THRESHOLD_PX = 80;

interface SourcePage {
  page: number;
  sections: string[];
  snippets: string[];
}

/**
 * Presentation only: groups the backend's `sources` by page (first-appearance
 * order), drops repeated snippets and the uninformative "other" section label.
 * The underlying source list is not altered.
 */
function groupSources(sources: SourceItem[]): SourcePage[] {
  const pages = new Map<number, SourcePage>();
  for (const s of sources) {
    let entry = pages.get(s.page_no);
    if (!entry) {
      entry = { page: s.page_no, sections: [], snippets: [] };
      pages.set(s.page_no, entry);
    }
    if (s.section_type && s.section_type !== "other" && !entry.sections.includes(s.section_type)) {
      entry.sections.push(s.section_type);
    }
    if (s.snippet && !entry.snippets.includes(s.snippet)) entry.snippets.push(s.snippet);
  }
  return [...pages.values()];
}

function Sources({ sources }: { sources: SourceItem[] }) {
  const pages = groupSources(sources);
  return (
    <details className="chat-sources">
      <summary>
        Sources · {pages.length === 1 ? "page" : "pages"}{" "}
        {pages.map((p) => p.page).join(", ")}
      </summary>
      {pages.map((p) => (
        <div key={p.page} className="chat-source-page">
          <div className="chat-source-title">
            <strong>Page {p.page}</strong>
            {p.sections.length > 0 && <span className="muted"> · {p.sections.join(", ")}</span>}
          </div>
          {p.snippets.map((snippet, i) => (
            <blockquote key={i}>{snippet}</blockquote>
          ))}
        </div>
      ))}
    </details>
  );
}

/** Presentational Chat tab; conversation state lives in App's useChat hook. */
export function ChatPanel({ chat }: ChatPanelProps) {
  const { messages, draft, setDraft, isSending, send, retry } = chat;
  const historyRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  function handleScroll() {
    const el = historyRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  }

  // Scroll to the newest entry: always right after the user sends (or
  // retries), otherwise only if they haven't scrolled up to read older
  // messages. Also runs on mount, so returning to the tab lands on the latest.
  useEffect(() => {
    const el = historyRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    if (stickToBottom.current || last?.role === "user") {
      el.scrollTop = el.scrollHeight;
      stickToBottom.current = true;
    }
  }, [messages, isSending]);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    send();
  }

  return (
    <div className="panel chat-panel">
      <h2>Ask about this document</h2>
      <div className="chat-history" ref={historyRef} onScroll={handleScroll}>
        {messages.map((m, i) => {
          if (m.role === "error") {
            const isLast = i === messages.length - 1;
            return (
              <div key={i} className="chat-message chat-error" role="alert">
                <p><strong>Couldn’t get an answer.</strong> {m.content}</p>
                {isLast && (
                  <button type="button" className="chat-retry" onClick={retry} disabled={isSending}>
                    Retry
                  </button>
                )}
              </div>
            );
          }
          return (
            <div key={i} className={`chat-message chat-${m.role}`}>
              <div className="chat-role">{m.role === "user" ? "You" : "Assistant"}</div>
              {m.role === "user" ? (
                <div className="chat-content"><p>{m.content}</p></div>
              ) : (
                <div className="chat-content">{renderMarkdown(m.content)}</div>
              )}
              {m.sources && m.sources.length > 0 && <Sources sources={m.sources} />}
              {m.warning && <p className="muted chat-warning">Warning: {m.warning}</p>}
            </div>
          );
        })}
        {isSending && (
          <div className="chat-message chat-assistant chat-loading" role="status" aria-live="polite">
            <div className="chat-role">Assistant</div>
            <p className="muted">
              Analyzing the document
              <span className="chat-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
            </p>
          </div>
        )}
        {messages.length === 0 && !isSending && (
          <p className="muted">No messages yet — ask a question below.</p>
        )}
      </div>

      <form onSubmit={handleSubmit} className="chat-input-row">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. What was the operating cash flow in 2024?"
          disabled={isSending}
        />
        <button type="submit" disabled={!draft.trim() || isSending}>
          {isSending ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
