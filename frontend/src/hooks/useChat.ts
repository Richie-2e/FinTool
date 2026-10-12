import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, postChat } from "../api/client";
import type { ChatResponse, ConversationTurn } from "../api/types";

export interface DisplayMessage {
  /** "error" entries are display-only: never sent back as conversation history. */
  role: "user" | "assistant" | "error";
  content: string;
  sources?: ChatResponse["sources"];
  warning?: string | null;
}

/**
 * Backend history: completed question/answer pairs only. A user turn that
 * never got an answer (failed request) and error entries are excluded, so a
 * failure never leaves dangling turns in later requests.
 */
function toHistory(messages: DisplayMessage[]): ConversationTurn[] {
  const history: ConversationTurn[] = [];
  messages.forEach((m, i) => {
    if (m.role === "assistant") {
      history.push({ role: m.role, content: m.content });
    } else if (m.role === "user" && messages[i + 1]?.role === "assistant") {
      history.push({ role: m.role, content: m.content });
    }
  });
  return history;
}

/**
 * Conversation state for the Chat tab. Owned by App (not ChatPanel) so it
 * survives tab switches -- ChatPanel is unmounted whenever another tab is
 * active -- and so a response that arrives while another tab is showing is
 * still appended. State is cleared whenever docId changes (new upload / reset).
 */
export function useChat(docId: string | null) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);

  // Bumped on every reset; a response from an older generation is discarded
  // so a slow reply can't leak into a different document's conversation.
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    setMessages([]);
    setDraft("");
    setIsSending(false);
  }, [docId]);

  /** Ask `question` on top of `base` (the transcript before this question). */
  const submit = useCallback(async (question: string, base: DisplayMessage[]) => {
    if (!docId) return;
    const history = toHistory(base);
    const myGeneration = generation.current;

    setMessages([...base, { role: "user", content: question }]);
    setIsSending(true);

    try {
      const response = await postChat({ doc_id: docId, question, conversation_history: history });
      if (generation.current !== myGeneration) return;
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.answer, sources: response.sources, warning: response.warning },
      ]);
    } catch (err) {
      if (generation.current !== myGeneration) return;
      const message = err instanceof ApiError ? err.message : "Chat request failed.";
      setMessages((prev) => [...prev, { role: "error", content: message }]);
    } finally {
      if (generation.current === myGeneration) setIsSending(false);
    }
  }, [docId]);

  const send = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed || isSending) return;
    setDraft("");
    void submit(trimmed, messages);
  }, [draft, isSending, messages, submit]);

  /** Re-ask the question whose request failed (last entry is an error). */
  const retry = useCallback(() => {
    if (isSending) return;
    const failed = messages[messages.length - 2];
    if (messages[messages.length - 1]?.role !== "error" || failed?.role !== "user") return;
    void submit(failed.content, messages.slice(0, -2));
  }, [isSending, messages, submit]);

  return { messages, draft, setDraft, isSending, send, retry };
}

export type ChatState = ReturnType<typeof useChat>;
