import { useEffect, useState } from "react";
import { ApiError, getMetrics, getRatios, getRisks } from "../api/client";
import type { MetricsResponse, RatiosResponse, RisksResponse, VerificationState } from "../api/types";
import { VerificationBadge } from "./VerificationBadge";
import { formatMetricValue, formatRatioValue } from "../utils/format";

interface DashboardProps {
  docId: string;
  onNavigate: (tab: "metrics" | "ratios" | "risks") => void;
  onExplain: (metricName: string, year: number) => void;
}

// Curated subset of the canonical metric names (extraction/canonical_metrics.py)
// and RatioItem fields (backend/models/schemas.py). Chosen for broad
// availability across the processed documents inspected before writing this
// list (revenue/net_profit/total_assets/operating_cash_flow are the most
// commonly resolved raw metrics; current_ratio/debt_to_equity/profit_margin
// double as the 3 ratios GET /explain already risk-classifies, plus
// free_cash_flow for a cash-generation signal). A metric or ratio missing
// for a given document/year is simply omitted, never fabricated.
const HEADLINE_METRICS: { name: string; label: string }[] = [
  { name: "revenue", label: "Revenue" },
  { name: "net_profit", label: "Net Profit" },
  { name: "total_assets", label: "Total Assets" },
  { name: "operating_cash_flow", label: "Operating Cash Flow" },
];

const KEY_RATIOS: { name: string; label: string }[] = [
  { name: "current_ratio", label: "Current Ratio" },
  { name: "debt_to_equity", label: "Debt to Equity" },
  { name: "profit_margin", label: "Profit Margin" },
  { name: "free_cash_flow", label: "Free Cash Flow" },
];

interface DashboardData {
  metrics: MetricsResponse;
  ratios: RatiosResponse;
  risks: RisksResponse;
}

/**
 * Dashboard V1 — a frontend-only composition of GET /metrics, GET /ratios,
 * and GET /risks (unchanged, no new endpoint). Every figure shown here is a
 * value one of those three endpoints already returns; this component only
 * curates which fields to surface first and links onward to the full detail
 * surfaces. No new computation, aggregation, or classification happens here.
 */
export function Dashboard({ docId, onNavigate, onExplain }: DashboardProps) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setSelectedYear(null);

    Promise.all([getMetrics(docId), getRatios(docId), getRisks(docId)])
      .then(([metrics, ratios, risks]) => {
        if (cancelled) return;
        setData({ metrics, ratios, risks });
        const years = metrics.document_years;
        if (years.length > 0) setSelectedYear(Math.max(...years));
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load dashboard.");
      });

    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="loading">Loading dashboard…</p>;

  const { metrics, ratios, risks } = data;
  const years = metrics.document_years;
  const year = selectedYear ?? (years.length > 0 ? Math.max(...years) : null);

  const headlineItems = year !== null
    ? HEADLINE_METRICS.map((h) => {
        const row = metrics.metrics.find((m) => m.metric_name === h.name && m.year === year);
        return { ...h, value: row?.value ?? null, state: row?.verification_state ?? null };
      }).filter((item) => item.value !== null)
    : [];

  const ratioRow = year !== null ? ratios.ratios.find((r) => r.year === year) : undefined;
  const ratioItems = ratioRow
    ? KEY_RATIOS.map((k) => ({
        ...k,
        value: (ratioRow[k.name as keyof typeof ratioRow] as number | null) ?? null,
        state: ratioRow.verification_states[k.name] ?? null,
      })).filter((item) => item.value !== null)
    : [];

  const riskItem = year !== null ? risks.risks.find((r) => r.year === year) : undefined;

  const trustStates: (VerificationState | null)[] = [
    ...headlineItems.map((i) => i.state),
    ...ratioItems.map((i) => i.state),
  ];
  const verifiedCount = trustStates.filter((s) => s === "VERIFIED").length;
  const needsReviewCount = trustStates.filter((s) => s === "NEEDS_REVIEW").length;
  const unverifiedCount = trustStates.length - verifiedCount - needsReviewCount;

  return (
    <div className="panel dashboard">
      <div className="panel-header">
        <div>
          <h2>{metrics.company_name ?? "Unknown company"}</h2>
          <p className="muted">doc_id: {metrics.doc_id}</p>
        </div>
        {years.length > 1 && (
          <div className="dashboard-year-selector">
            {[...years].sort((a, b) => b - a).map((y) => (
              <button
                key={y}
                className={y === year ? "active" : ""}
                onClick={() => setSelectedYear(y)}
              >
                {y}
              </button>
            ))}
          </div>
        )}
      </div>

      {year === null && <p className="muted">No document years available for this document.</p>}

      {year !== null && (
        <div className="dashboard-grid">
          <div className="dashboard-card">
            <div className="dashboard-card__title">Headline metrics ({year})</div>
            {headlineItems.length === 0 && <p className="muted">No headline metrics resolved for {year}.</p>}
            <ul className="dashboard-item-list">
              {headlineItems.map((item) => (
                <li key={item.name} className="dashboard-item-list__row">
                  <span>{item.label}</span>
                  <span className="dashboard-item-list__value">
                    {formatMetricValue(item.value)}
                    <VerificationBadge state={item.state} compact />
                  </span>
                </li>
              ))}
            </ul>
            <button className="link-button dashboard-card__footer" onClick={() => onNavigate("metrics")}>
              View all metrics →
            </button>
          </div>

          <div className="dashboard-card">
            <div className="dashboard-card__title">Key ratios ({year})</div>
            {ratioItems.length === 0 && <p className="muted">No key ratios computed for {year}.</p>}
            <ul className="dashboard-item-list">
              {ratioItems.map((item) => (
                <li
                  key={item.name}
                  className="dashboard-item-list__row clickable-row"
                  onClick={() => onExplain(item.name, year)}
                >
                  <span>{item.label}</span>
                  <span className="dashboard-item-list__value">
                    {formatRatioValue(item.value)}
                    <VerificationBadge state={item.state} compact />
                  </span>
                </li>
              ))}
            </ul>
            <button className="link-button dashboard-card__footer" onClick={() => onNavigate("ratios")}>
              View all ratios →
            </button>
          </div>

          <div className="dashboard-card">
            <div className="dashboard-card__title">Risk ({year})</div>
            {!riskItem && <p className="muted">No risk classification available for {year}.</p>}
            {riskItem && (
              <>
                <div className="dashboard-overall-risk">
                  <span className={`risk-level risk-level--${riskItem.overall_risk.toLowerCase()} risk-level--overall`}>
                    Overall: {riskItem.overall_risk}
                  </span>
                  <VerificationBadge state={riskItem.verification_states.overall ?? null} compact />
                </div>
                <ul className="dashboard-item-list">
                  <li className="dashboard-item-list__row">
                    <span>Liquidity</span>
                    <span className={`risk-level risk-level--${riskItem.liquidity_risk.toLowerCase()}`}>{riskItem.liquidity_risk}</span>
                  </li>
                  <li className="dashboard-item-list__row">
                    <span>Debt</span>
                    <span className={`risk-level risk-level--${riskItem.debt_risk.toLowerCase()}`}>{riskItem.debt_risk}</span>
                  </li>
                  <li className="dashboard-item-list__row">
                    <span>Profitability</span>
                    <span className={`risk-level risk-level--${riskItem.profitability_risk.toLowerCase()}`}>{riskItem.profitability_risk}</span>
                  </li>
                  <li className="dashboard-item-list__row">
                    <span>Cash flow</span>
                    <span className={`risk-level risk-level--${riskItem.cashflow_risk.toLowerCase()}`}>{riskItem.cashflow_risk}</span>
                  </li>
                </ul>
              </>
            )}
            <button className="link-button dashboard-card__footer" onClick={() => onNavigate("risks")}>
              View risk analysis →
            </button>
          </div>
        </div>
      )}

      {trustStates.length > 0 && (
        <p className="dashboard-trust-summary muted">
          Data quality (this view): {verifiedCount} verified, {needsReviewCount} needs review, {unverifiedCount} unverified
          — out of {trustStates.length} figures shown above.
        </p>
      )}
    </div>
  );
}
