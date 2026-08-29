/**
 * Display-only number formatting. Never mutates or rounds the underlying
 * value that gets sent anywhere — these are pure presentation helpers,
 * called only at render time.
 */

/** Thousands-separated, no more than 2 decimal places, trailing .00 dropped. */
export function formatMetricValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
}

/** Ratios/percentages: fixed at 2 decimal places for consistent column alignment. */
export function formatRatioValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}
