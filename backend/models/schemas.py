from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class UploadResponse(_Base):
    doc_id: str
    pdf_name: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StatusResponse(_Base):
    doc_id: str
    status: str
    progress_message: Optional[str] = None
    company_name: Optional[str] = None
    document_years: Optional[list[int]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class MetricItem(_Base):
    metric_name: str
    year: int
    value: Optional[float] = None
    unit: Optional[str] = None
    page_no: Optional[int] = None
    raw_label: Optional[str] = None
    confidence: Optional[str] = None
    statement_type: Optional[str] = None
    section_type: Optional[str] = None


class QualityReport(_Base):
    candidate_rows: int
    resolved_rows: int
    missing_core_metrics_by_year: dict[str, Any]
    issues: list[Any]


class MetricsResponse(_Base):
    doc_id: str
    company_name: Optional[str] = None
    document_years: list[int]
    metrics: list[MetricItem]
    quality_report: Optional[QualityReport] = None


# ---------------------------------------------------------------------------
# Ratios
# ---------------------------------------------------------------------------

class RatioItem(_Base):
    year: int
    current_ratio: Optional[float] = None
    cash_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    debt_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None
    profit_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    asset_turnover: Optional[float] = None
    ocf_to_revenue: Optional[float] = None
    free_cash_flow: Optional[float] = None
    yoy_revenue_growth: Optional[float] = None
    yoy_profit_growth: Optional[float] = None
    total_debt: Optional[float] = None


class RatiosResponse(_Base):
    doc_id: str
    company_name: Optional[str] = None
    ratios: list[RatioItem]


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------

class RiskItem(_Base):
    year: int

    liquidity_risk: str
    liquidity_ratio_used: Optional[str] = None
    liquidity_ratio_value: Optional[float] = None
    liquidity_threshold: Optional[str] = None

    debt_risk: str
    debt_ratio_used: Optional[str] = None
    debt_ratio_value: Optional[float] = None
    debt_threshold: Optional[str] = None

    profitability_risk: str
    profitability_ratio_used: Optional[str] = None
    profitability_ratio_value: Optional[float] = None
    profitability_threshold: Optional[str] = None

    cashflow_risk: str
    cashflow_value: Optional[float] = None
    cashflow_threshold: Optional[str] = None

    overall_risk: str


class RisksResponse(_Base):
    doc_id: str
    company_name: Optional[str] = None
    risks: list[RiskItem]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ConversationTurn(_Base):
    role: str
    content: str


class ChatRequest(_Base):
    doc_id: str
    question: str
    conversation_history: list[ConversationTurn] = []


class SourceItem(_Base):
    page_no: int
    section_type: Optional[str] = None
    snippet: Optional[str] = None


class MetricUsed(_Base):
    metric: str
    year: int
    value: Optional[float] = None


class ChatResponse(_Base):
    answer: str
    sources: list[SourceItem]
    metrics_used: list[MetricUsed]
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Explain
# ---------------------------------------------------------------------------

class ExplainInput(_Base):
    metric: str
    value: Optional[float] = None
    unit: Optional[str] = None
    page_no: Optional[int] = None
    raw_label: Optional[str] = None
    source: Optional[str] = None


class RiskClassification(_Base):
    risk_type: str
    level: str
    threshold_applied: str


class ExplainResponse(_Base):
    metric_name: str
    formula: str
    year: int
    result: Optional[float] = None
    inputs: list[ExplainInput]
    risk_classification: Optional[RiskClassification] = None


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class ErrorResponse(_Base):
    detail: str
    error_code: str
    doc_id: Optional[str] = None
