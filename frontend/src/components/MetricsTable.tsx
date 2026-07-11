import { useEffect, useState } from "react";
import { ApiError, getMetrics } from "../api/client";
import type { MetricsResponse } from "../api/types";

interface MetricsTableProps {
  docId: string;
}

/**
 * Step 3: GET /metrics/{doc_id} and render every resolved metric row.
 *
 * Read-only: raw resolved metrics (net_profit, revenue, ...) are not
 * explainable via GET /explain — that endpoint only has provenance for
 * computed ratios. The Explain interaction lives on <RatiosTable /> instead.
 */
export function MetricsTable({ docId }: MetricsTableProps) {
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMetrics(docId)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load metrics."));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading metrics…</p>;

  return (
    <div className="panel">
      <h2>Extracted metrics — {data.company_name ?? "Unknown company"}</h2>
      {data.quality_report && (
        <p className="muted">
          {data.quality_report.resolved_rows} of {data.quality_report.candidate_rows} extracted candidates resolved.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>Year</th>
            <th>Value</th>
            <th>Unit</th>
            <th>Statement</th>
            <th>Page</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {data.metrics.map((m, i) => (
            <tr key={i}>
              <td>{m.metric_name}</td>
              <td>{m.year}</td>
              <td>{m.value ?? "—"}</td>
              <td>{m.unit ?? "—"}</td>
              <td>{m.statement_type ?? "—"}</td>
              <td>{m.page_no ?? "—"}</td>
              <td>{m.confidence ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.metrics.length === 0 && <p className="muted">No metrics were resolved for this document.</p>}
    </div>
  );
}
