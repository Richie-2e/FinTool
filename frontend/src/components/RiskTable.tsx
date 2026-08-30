import { useEffect, useState } from "react";
import { ApiError, getRisks } from "../api/client";
import type { RiskItem, RisksResponse } from "../api/types";
import { VerificationBadge } from "./VerificationBadge";
import { formatRatioValue } from "../utils/format";

interface RiskTableProps {
  docId: string;
}

interface RiskRow {
  key: "liquidity" | "debt" | "profitability" | "cashflow";
  label: string;
  level: string;
  ratioUsed: string | null;
  ratioValue: number | null;
  threshold: string | null;
}

function buildRows(item: RiskItem): RiskRow[] {
  return [
    {
      key: "liquidity",
      label: "Liquidity",
      level: item.liquidity_risk,
      ratioUsed: item.liquidity_ratio_used,
      ratioValue: item.liquidity_ratio_value,
      threshold: item.liquidity_threshold,
    },
    {
      key: "debt",
      label: "Debt",
      level: item.debt_risk,
      ratioUsed: item.debt_ratio_used,
      ratioValue: item.debt_ratio_value,
      threshold: item.debt_threshold,
    },
    {
      key: "profitability",
      label: "Profitability",
      level: item.profitability_risk,
      ratioUsed: item.profitability_ratio_used,
      ratioValue: item.profitability_ratio_value,
      threshold: item.profitability_threshold,
    },
    {
      key: "cashflow",
      label: "Cash flow",
      level: item.cashflow_risk,
      ratioUsed: "operating_cash_flow",
      ratioValue: item.cashflow_value,
      threshold: item.cashflow_threshold,
    },
  ];
}

/**
 * Consolidated risk view — all 5 categories, every year GET /risks/{doc_id}
 * returns. Every level, ratio value, threshold, and verification badge here
 * is rendered exactly as the backend returns it; nothing is computed,
 * reclassified, or upgraded client-side. "Unknown" is a real backend risk
 * level (the underlying ratio was null, not "risk is neutral" — see
 * docs/API.md) and is rendered as its own distinct state, never hidden.
 */
export function RiskTable({ docId }: RiskTableProps) {
  const [data, setData] = useState<RisksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRisks(docId)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load risk analysis."));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="loading">Loading risk analysis…</p>;

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Risk analysis</h2>
        <span className="muted">{data.company_name ?? "Unknown company"}</span>
      </div>
      {data.risks.length === 0 && <p className="muted">No risk classification available for this document.</p>}
      {data.risks.map((item) => (
        <RiskYearSection key={item.year} item={item} />
      ))}
    </div>
  );
}

function RiskYearSection({ item }: { item: RiskItem }) {
  const rows = buildRows(item);
  const overallState = item.verification_states.overall ?? null;

  return (
    <section className="risk-year">
      <div className="risk-year__header">
        <h3>{item.year}</h3>
        <span className={`risk-level risk-level--${item.overall_risk.toLowerCase()} risk-level--overall`}>
          Overall: {item.overall_risk}
          <VerificationBadge state={overallState} compact />
        </span>
      </div>
      <table className="risk-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>Level</th>
            <th className="numeric-col">Value used</th>
            <th>Threshold</th>
            <th>Verification</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td className="metric-name-cell">{row.label}</td>
              <td>
                <span className={`risk-level risk-level--${row.level.toLowerCase()}`}>{row.level}</span>
              </td>
              <td className="numeric-col">
                {row.ratioValue !== null ? formatRatioValue(row.ratioValue) : "—"}
                {row.ratioUsed && <span className="unit-suffix"> ({formatLabel(row.ratioUsed)})</span>}
              </td>
              <td className="muted">{row.threshold ?? "—"}</td>
              <td>
                <VerificationBadge state={item.verification_states[row.key] ?? null} compact />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function formatLabel(name: string): string {
  return name.replace(/_/g, " ");
}
