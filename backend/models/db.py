from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend.config import DATABASE_URL


# ---------------------------------------------------------------------------
# Base & engine
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[str]          = mapped_column(Text, primary_key=True)
    pdf_name: Mapped[str]        = mapped_column(Text, nullable=False)
    company_name: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    page_count: Mapped[Optional[int]]    = mapped_column(Integer, nullable=True)
    document_years: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # JSON array
    upload_time: Mapped[datetime]        = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[str]          = mapped_column(Text, default="processing")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_dir: Mapped[Optional[str]]    = mapped_column(Text, nullable=True)
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ResolvedMetric(Base):
    __tablename__ = "resolved_metrics"

    id: Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str]          = mapped_column(Text, ForeignKey("documents.doc_id"), nullable=False)
    metric_name: Mapped[str]     = mapped_column(Text, nullable=False)
    value: Mapped[Optional[float]]       = mapped_column(Float, nullable=True)
    unit: Mapped[Optional[str]]          = mapped_column(Text, nullable=True)
    year: Mapped[Optional[int]]          = mapped_column(Integer, nullable=True)
    page_no: Mapped[Optional[int]]       = mapped_column(Integer, nullable=True)
    raw_label: Mapped[Optional[str]]     = mapped_column(Text, nullable=True)
    statement_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    section_type: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[str]]    = mapped_column(Text, nullable=True)

    # Phase A (next-architecture review): evidence is populated now — the
    # verbatim snippet CandidateMetric.evidence already computes and L4/L5
    # already validate, previously discarded before persistence.
    evidence: Mapped[Optional[str]]      = mapped_column(Text, nullable=True)

    # Structural provenance + verification state -- columns prepared now so
    # later phases (B: structural tables, C: L6 validation) only need to
    # populate them, not migrate the schema again. Unpopulated (NULL) until
    # then; no current code path writes to them.
    table_id: Mapped[Optional[str]]            = mapped_column(Text, nullable=True)
    row_index: Mapped[Optional[int]]           = mapped_column(Integer, nullable=True)
    col_index: Mapped[Optional[int]]           = mapped_column(Integer, nullable=True)
    verification_state: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    verification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ComputedMetric(Base):
    __tablename__ = "computed_metrics"

    id: Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str]          = mapped_column(Text, ForeignKey("documents.doc_id"), nullable=False)
    year: Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)

    # Ratios
    current_ratio: Mapped[Optional[float]]      = mapped_column(Float, nullable=True)
    cash_ratio: Mapped[Optional[float]]         = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[Optional[float]]     = mapped_column(Float, nullable=True)
    debt_ratio: Mapped[Optional[float]]         = mapped_column(Float, nullable=True)
    interest_coverage: Mapped[Optional[float]]  = mapped_column(Float, nullable=True)
    profit_margin: Mapped[Optional[float]]      = mapped_column(Float, nullable=True)
    operating_margin: Mapped[Optional[float]]   = mapped_column(Float, nullable=True)
    gross_margin: Mapped[Optional[float]]       = mapped_column(Float, nullable=True)
    asset_turnover: Mapped[Optional[float]]     = mapped_column(Float, nullable=True)
    ocf_to_revenue: Mapped[Optional[float]]     = mapped_column(Float, nullable=True)
    free_cash_flow: Mapped[Optional[float]]     = mapped_column(Float, nullable=True)
    yoy_revenue_growth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yoy_profit_growth: Mapped[Optional[float]]  = mapped_column(Float, nullable=True)
    total_debt: Mapped[Optional[float]]         = mapped_column(Float, nullable=True)

    # Risk labels
    liquidity_risk: Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    debt_risk: Mapped[Optional[str]]           = mapped_column(Text, nullable=True)
    profitability_risk: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    cashflow_risk: Mapped[Optional[str]]       = mapped_column(Text, nullable=True)
    overall_risk: Mapped[Optional[str]]        = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_tables() -> None:
    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
