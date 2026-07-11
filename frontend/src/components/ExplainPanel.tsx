import { useEffect, useState } from "react";
import { ApiError, getExplain } from "../api/client";
import type { ExplainResponse } from "../api/types";

interface ExplainPanelProps {
  docId: string;
  metricName: string;
  year: number;
  onClose: () => void;
}

/**
 * Step 6: GET /explain/{doc_id}/{metric_name}?year={year}.
 * Rendered as a dismissible side panel, opened from a ratio cell in
 * <RatiosTable />.
 */
export function ExplainPanel({ docId, metricName, year, onClose }: ExplainPanelProps) {
  const [data, setData] = useState<ExplainResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    getExplain(docId, metricName, year)
      .then((result) => !cancelled && setData(result))
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to load explanation.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [docId, metricName, year]);

  return (
    <aside className="panel explain-panel">
      <div className="explain-header">
        <h2>Explain: {metricName} ({year})</h2>
        <button onClick={onClose} aria-label="Close">×</button>
      </div>

      {error && <p className="error">{error}</p>}

      {!error && !data && <p>Loading…</p>}

      {data && (
        <>
          <p><strong>Formula:</strong> {data.formula}</p>
          <p><strong>Result:</strong> {data.result ?? "—"}</p>

          <h3>Inputs</h3>
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Page</th>
                <th>Raw label</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {data.inputs.map((input, i) => (
                <tr key={i}>
                  <td>{input.metric}</td>
                  <td>{input.value ?? "—"}</td>
                  <td>{input.unit ?? "—"}</td>
                  <td>{input.page_no ?? "—"}</td>
                  <td>{input.raw_label ?? "—"}</td>
                  <td>{input.source ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.risk_classification && (
            <p>
              <strong>Risk:</strong> {data.risk_classification.level} ({data.risk_classification.risk_type}) —{" "}
              {data.risk_classification.threshold_applied}
            </p>
          )}
        </>
      )}
    </aside>
  );
}
