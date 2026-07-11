import { useEffect, useState } from "react";
import { ApiError, getRatios } from "../api/client";
import type { RatioItem, RatiosResponse } from "../api/types";

interface RatiosTableProps {
  docId: string;
  /** Cell click hands off to <ExplainPanel /> in the parent. */
  onExplain: (metricName: string, year: number) => void;
}

// Every numeric key of RatioItem except `year`. Doubles as the column list.
const RATIO_COLUMNS: (keyof Omit<RatioItem, "year">)[] = [
  "current_ratio",
  "cash_ratio",
  "debt_to_equity",
  "debt_ratio",
  "interest_coverage",
  "profit_margin",
  "operating_margin",
  "gross_margin",
  "asset_turnover",
  "ocf_to_revenue",
  "free_cash_flow",
  "yoy_revenue_growth",
  "yoy_profit_growth",
  "total_debt",
];

/**
 * Step 4: GET /ratios/{doc_id} and render computed ratios in a year x ratio
 * grid. Every non-null cell is clickable — it opens <ExplainPanel /> for
 * that ratio/year via GET /explain/{doc_id}/{metric}?year={year}.
 */
export function RatiosTable({ docId, onExplain }: RatiosTableProps) {
  const [data, setData] = useState<RatiosResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRatios(docId)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load ratios."));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading ratios…</p>;

  return (
    <div className="panel">
      <h2>Computed ratios — {data.company_name ?? "Unknown company"}</h2>
      <p className="muted">Click a value to see its formula and source (GET /explain).</p>
      <table>
        <thead>
          <tr>
            <th>Ratio</th>
            {data.ratios.map((r) => (
              <th key={r.year}>{r.year}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {RATIO_COLUMNS.map((column) => (
            <tr key={column}>
              <td>{column}</td>
              {data.ratios.map((r) => {
                const value = r[column];
                return (
                  <td
                    key={r.year}
                    className={value !== null ? "clickable-cell" : undefined}
                    onClick={() => value !== null && onExplain(column, r.year)}
                  >
                    {value ?? "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {data.ratios.length === 0 && <p className="muted">No computed ratios for this document.</p>}
    </div>
  );
}
