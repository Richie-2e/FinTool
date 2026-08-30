/**
 * TypeScript mirrors of backend/models/schemas.py.
 *
 * Each interface below corresponds 1:1 to a Pydantic model in that file —
 * same name, same fields, same optionality. If a backend response model
 * changes, update the matching interface here to keep them in sync.
 */

/**
 * The backend's L6 structural-verification outcome for a resolved value.
 * Mirrors extraction/llm/structural_validator.py's state model exactly —
 * this is the ONLY place verification state is computed; the frontend
 * never derives, infers, or overrides it. `null` means "not structurally
 * checked" (e.g. no table was available for that page), not "unverified
 * and therefore wrong" — see VerificationBadge's docstring for the exact
 * user-facing wording this maps to.
 */
export type VerificationState = "VERIFIED" | "NEEDS_REVIEW" | null;

// --- Upload (UploadResponse) -----------------------------------------------

export interface UploadResponse {
  doc_id: string;
  pdf_name: string;
  status: string;
  message: string;
}

// --- Status (StatusResponse) ------------------------------------------------

export interface StatusResponse {
  doc_id: string;
  status: string;
  progress_message: string | null;
  company_name: string | null;
  document_years: number[] | null;
  error: string | null;
}

// --- Metrics (MetricItem, QualityReport, MetricsResponse) -------------------

export interface MetricItem {
  metric_name: string;
  year: number;
  value: number | null;
  unit: string | null;
  page_no: number | null;
  raw_label: string | null;
  confidence: string | null;
  statement_type: string | null;
  section_type: string | null;
  // Already returned by GET /metrics today (backend/models/schemas.py
  // MetricItem, via ResolvedMetric -> MetricItem.model_validate) — these
  // fields were simply not declared here before, so the frontend silently
  // discarded them. See TRUST_PROVENANCE_UX_ARCHITECTURE_REVIEW.md.
  evidence: string | null;
  table_id: string | null;
  row_index: number | null;
  col_index: number | null;
  verification_state: VerificationState;
  verification_reason: string | null;
}

export interface QualityReport {
  candidate_rows: number;
  resolved_rows: number;
  missing_core_metrics_by_year: Record<string, unknown>;
  issues: unknown[];
}

export interface MetricsResponse {
  doc_id: string;
  company_name: string | null;
  document_years: number[];
  metrics: MetricItem[];
  quality_report: QualityReport | null;
}

// --- Ratios (RatioItem, RatiosResponse) --------------------------------------

export interface RatioItem {
  year: number;
  current_ratio: number | null;
  cash_ratio: number | null;
  debt_to_equity: number | null;
  debt_ratio: number | null;
  interest_coverage: number | null;
  profit_margin: number | null;
  operating_margin: number | null;
  gross_margin: number | null;
  asset_turnover: number | null;
  ocf_to_revenue: number | null;
  free_cash_flow: number | null;
  yoy_revenue_growth: number | null;
  yoy_profit_growth: number | null;
  total_debt: number | null;
  // Already returned by GET /ratios today — worst-case verification_state
  // per ratio name, keyed exactly as the numeric fields above (e.g.
  // "debt_ratio"). Absent/null means "not computable" (no provenance entry
  // for that ratio's inputs), never a claim about correctness.
  verification_states: Record<string, VerificationState>;
}

export interface RatiosResponse {
  doc_id: string;
  company_name: string | null;
  ratios: RatioItem[];
}

// --- Risks (RiskItem, RisksResponse) -----------------------------------------

export interface RiskItem {
  year: number;

  liquidity_risk: string;
  liquidity_ratio_used: string | null;
  liquidity_ratio_value: number | null;
  liquidity_threshold: string | null;

  debt_risk: string;
  debt_ratio_used: string | null;
  debt_ratio_value: number | null;
  debt_threshold: string | null;

  profitability_risk: string;
  profitability_ratio_used: string | null;
  profitability_ratio_value: number | null;
  profitability_threshold: string | null;

  cashflow_risk: string;
  cashflow_value: number | null;
  cashflow_threshold: string | null;

  overall_risk: string;

  // Keys: "liquidity" | "debt" | "profitability" | "cashflow" | "overall".
  verification_states: Record<string, VerificationState>;
}

export interface RisksResponse {
  doc_id: string;
  company_name: string | null;
  risks: RiskItem[];
}

// --- Chat (ConversationTurn, ChatRequest, SourceItem, MetricUsed, ChatResponse) --

export interface ConversationTurn {
  role: string;
  content: string;
}

export interface ChatRequest {
  doc_id: string;
  question: string;
  conversation_history: ConversationTurn[];
}

export interface SourceItem {
  page_no: number;
  section_type: string | null;
  snippet: string | null;
}

export interface MetricUsed {
  metric: string;
  year: number;
  value: number | null;
}

export interface ChatResponse {
  answer: string;
  sources: SourceItem[];
  metrics_used: MetricUsed[];
  warning: string | null;
}

// --- Explain (ExplainInput, RiskClassification, ExplainResponse) ------------

export interface ExplainInput {
  metric: string;
  value: number | null;
  unit: string | null;
  page_no: number | null;
  raw_label: string | null;
  source: string | null;
  // Already returned by GET /explain today — see MetricItem above for the
  // same fields' provenance.
  evidence: string | null;
  verification_state: VerificationState;
  verification_reason: string | null;
  table_id: string | null;
  row_index: number | null;
  col_index: number | null;
}

export interface RiskClassification {
  risk_type: string;
  level: string;
  threshold_applied: string;
}

export interface ExplainResponse {
  metric_name: string;
  formula: string;
  year: number;
  result: number | null;
  inputs: ExplainInput[];
  risk_classification: RiskClassification | null;
  // Already returned by GET /explain today — worst-case verification_state
  // across this ratio's own inputs.
  verification_state: VerificationState;
}

// --- Error (ErrorResponse) ---------------------------------------------------

export interface ErrorResponse {
  detail: string;
  error_code: string;
  doc_id: string | null;
}
