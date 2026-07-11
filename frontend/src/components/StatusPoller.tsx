import { useEffect, useRef, useState } from "react";
import { ApiError, getStatus } from "../api/client";
import type { StatusResponse } from "../api/types";

const POLL_INTERVAL_MS = 3000;

interface StatusPollerProps {
  docId: string;
  /** Called once when status becomes "ready". */
  onReady: (status: StatusResponse) => void;
}

/**
 * Step 2 of the workflow: poll GET /status/{doc_id} until the backend
 * reports "ready" (success) or "failed" (stop and show the error).
 */
export function StatusPoller({ docId, onReady }: StatusPollerProps) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  // Guards against calling onReady more than once (e.g. after React 18
  // StrictMode's double-invoke in development).
  const hasFiredReady = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const result = await getStatus(docId);
        if (cancelled) return;

        setStatus(result);

        if (result.status === "ready" && !hasFiredReady.current) {
          hasFiredReady.current = true;
          onReady(result);
          return; // stop polling
        }
        if (result.status === "failed") {
          return; // stop polling, error is shown from `status.error`
        }
        setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        setPollError(err instanceof ApiError ? err.message : "Lost connection to backend while polling status.");
      }
    }

    poll();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  if (pollError) {
    return <p className="error">{pollError}</p>;
  }

  return (
    <div className="panel">
      <h2>Processing…</h2>
      <p>doc_id: <code>{docId}</code></p>
      <p>{status?.progress_message ?? "Waiting for status…"}</p>
      {status?.status === "failed" && <p className="error">Processing failed: {status.error}</p>}
      <p className="muted">
        First-time processing runs the full extraction + RAG-indexing pipeline and can take several minutes.
      </p>
    </div>
  );
}
