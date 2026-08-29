import { Fragment, useEffect, useState } from "react";
import { ApiError, getMetrics } from "../api/client";
import type { MetricItem, MetricsResponse } from "../api/types";
import { VerificationBadge } from "./VerificationBadge";
import { formatMetricValue } from "../utils/format";

interface MetricsTableProps {
  docId: string;
}

/**
 * Step 3: GET /metrics/{doc_id} and render every resolved metric row.
 *
 * Read-only: raw resolved metrics (net_profit, revenue, ...) are not
 * explainable via GET /explain — that endpoint only has provenance for
 * computed ratios. The Explain interaction lives on <RatiosTable /> instead.
 *
 * Each row's verification badge and expandable detail render exactly what
 * the backend already returns (evidence, table/row/column, verification
 * reason) — nothing here is computed, inferred, or upgraded client-side.
 */
export function MetricsTable({ docId }: MetricsTableProps) {
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  useEffect(() => {
    let cancelled = false;
    getMetrics(docId)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load metrics."));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  function toggleRow(index: number) {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="loading">Loading metrics…</p>;

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Extracted metrics</h2>
        <span className="muted">{data.company_name ?? "Unknown company"}</span>
      </div>
      {data.quality_report && (
        <p className="muted">
          {data.quality_report.resolved_rows} of {data.quality_report.candidate_rows} extracted candidates resolved.
        </p>
      )}
      <table className="metrics-table">
        <thead>
          <tr>
            <th aria-hidden="true"></th>
            <th>Metric</th>
            <th>Year</th>
            <th className="numeric-col">Value</th>
            <th>Statement</th>
            <th>Verification</th>
          </tr>
        </thead>
        <tbody>
          {data.metrics.map((m, i) => {
            const hasDetail = Boolean(m.evidence || m.page_no || m.table_id);
            const isExpanded = expandedRows.has(i);
            return (
              <Fragment key={i}>
                <tr
                  className={hasDetail ? "clickable-row" : undefined}
                  onClick={() => hasDetail && toggleRow(i)}
                >
                  <td className="expand-col">
                    {hasDetail && <span className={`chevron ${isExpanded ? "chevron--open" : ""}`}>›</span>}
                  </td>
                  <td className="metric-name-cell">{formatMetricLabel(m.metric_name)}</td>
                  <td>{m.year}</td>
                  <td className="numeric-col">
                    {formatMetricValue(m.value)}
                    {m.unit && <span className="unit-suffix"> {m.unit}</span>}
                  </td>
                  <td className="muted">{m.statement_type ?? "—"}</td>
                  <td>
                    <VerificationBadge state={m.verification_state} />
                  </td>
                </tr>
                {isExpanded && (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={5}>
                      <MetricDetail metric={m} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {data.metrics.length === 0 && <p className="muted">No metrics were resolved for this document.</p>}
    </div>
  );
}

/** Progressive-disclosure detail panel — only renders fields that actually
 * exist on this metric; never fabricates a page, table, or evidence string
 * that the backend didn't provide. */
function MetricDetail({ metric }: { metric: MetricItem }) {
  return (
    <dl className="metric-detail">
      <div className="metric-detail__row">
        <dt>Verification</dt>
        <dd>
          <VerificationBadge state={metric.verification_state} />
          {metric.verification_reason && <span className="metric-detail__reason"> — {metric.verification_reason}</span>}
        </dd>
      </div>
      {metric.evidence && (
        <div className="metric-detail__row">
          <dt>Evidence</dt>
          <dd className="metric-detail__evidence">{metric.evidence}</dd>
        </div>
      )}
      {(metric.page_no || metric.raw_label) && (
        <div className="metric-detail__row">
          <dt>Source</dt>
          <dd>
            {metric.page_no ? `Page ${metric.page_no}` : "—"}
            {metric.raw_label ? ` — as labeled: "${metric.raw_label}"` : ""}
          </dd>
        </div>
      )}
      {metric.table_id && (
        <div className="metric-detail__row">
          <dt>Structural location</dt>
          <dd>
            Table {metric.table_id}
            {metric.row_index !== null ? `, row ${metric.row_index}` : ""}
            {metric.col_index !== null ? `, column ${metric.col_index}` : ""}
          </dd>
        </div>
      )}
    </dl>
  );
}

function formatMetricLabel(metricName: string): string {
  return metricName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
