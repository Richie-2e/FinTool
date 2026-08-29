import { useEffect, useState } from "react";
import { ApiError, getExplain } from "../api/client";
import type { ExplainResponse } from "../api/types";
import { VerificationBadge } from "./VerificationBadge";
import { formatMetricValue, formatRatioValue } from "../utils/format";

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
 *
 * Every verification badge and evidence string here comes directly from
 * the backend response — this panel never computes or overrides
 * verification state.
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
        <h2>{formatMetricLabel(metricName)} ({year})</h2>
        <button onClick={onClose} aria-label="Close">×</button>
      </div>

      {error && <p className="error">{error}</p>}

      {!error && !data && <p className="loading">Loading…</p>}

      {data && (
        <>
          <div className="explain-summary">
            <p className="muted">{data.formula}</p>
            <p className="explain-result">
              {formatRatioValue(data.result)}
              <VerificationBadge state={data.verification_state} />
            </p>
          </div>

          <h3>Inputs</h3>
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th className="numeric-col">Value</th>
                <th>Page</th>
                <th>Verification</th>
              </tr>
            </thead>
            <tbody>
              {data.inputs.map((input, i) => (
                <tr key={i}>
                  <td>{formatMetricLabel(input.metric)}</td>
                  <td className="numeric-col">
                    {formatMetricValue(input.value)}
                    {input.unit && <span className="unit-suffix"> {input.unit}</span>}
                  </td>
                  <td className="muted">{input.page_no ?? "—"}</td>
                  <td><VerificationBadge state={input.verification_state} compact /></td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.inputs.some((i) => i.verification_reason || i.evidence || i.raw_label || i.source) && (
            <div className="explain-input-detail">
              <h3>Input detail</h3>
              <dl>
                {data.inputs.map((input, i) => (
                  <div key={i} className="metric-detail__row">
                    <dt>{formatMetricLabel(input.metric)}</dt>
                    <dd>
                      {(input.source || input.raw_label) && (
                        <p className="muted">
                          {input.source ? `Source: ${input.source}` : ""}
                          {input.source && input.raw_label ? " — " : ""}
                          {input.raw_label ? `as labeled: "${input.raw_label}"` : ""}
                        </p>
                      )}
                      {input.verification_reason && <p className="metric-detail__reason">{input.verification_reason}</p>}
                      {input.evidence && <p className="metric-detail__evidence">{input.evidence}</p>}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {data.risk_classification && (
            <p className="risk-note">
              <strong>Risk:</strong> {data.risk_classification.level} ({data.risk_classification.risk_type}) —{" "}
              {data.risk_classification.threshold_applied}
            </p>
          )}
        </>
      )}
    </aside>
  );
}

function formatMetricLabel(metricName: string): string {
  return metricName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
