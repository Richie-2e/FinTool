/**
 * TypeScript mirrors of backend/models/schemas.py.
 *
 * Each interface below corresponds 1:1 to a Pydantic model in that file —
 * same name, same fields, same optionality. If a backend response model
 * changes, update the matching interface here to keep them in sync.
 */

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
}

export interface RatiosResponse {
  doc_id: string;
  company_name: string | null;
  ratios: RatioItem[];
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
}

// --- Error (ErrorResponse) ---------------------------------------------------

export interface ErrorResponse {
  detail: string;
  error_code: string;
  doc_id: string | null;
}
