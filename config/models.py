"""Core Pydantic v2 models shared by every module.

Guarantees enforced here:
- All money is decimal.Decimal quantized to cents (ROUND_HALF_UP). Floats are rejected.
- All models are strict: no silent type coercion at module boundaries.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any

from dateutil import parser as date_parser
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.schemas import AuditTrailEntry

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")


def to_money(value: Any) -> Decimal:
    """Convert a string/int/Decimal into an exact Decimal rounded to cents.

    Floats are rejected outright: they may already carry binary rounding error,
    so accepting them would silently corrupt financial math.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(
            f"Float/bool money value rejected ({value!r}); money must arrive as a string"
        )
    if isinstance(value, Decimal):
        return value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if isinstance(value, int):
        return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return Decimal(cleaned).quantize(CENTS, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError(f"Cannot parse money value {value!r}") from exc
    raise ValueError(
        f"Unsupported money type {type(value).__name__} for value {value!r}"
    )


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date_parser.parse(value.strip()).date()
    raise ValueError(f"Cannot parse date value {value!r}")


class UseCase(StrEnum):
    LEASE_CAM = "lease_cam"
    FREIGHT = "freight"
    SAAS = "saas"
    EXPENSE = "expense"
    MEDICAL = "medical"


class Severity(StrEnum):
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    REVIEW = "REVIEW"
    DISPUTE = "DISPUTE"


class ErrorModel(BaseModel):
    """Clean, typed error record. Returned instead of raising — never crashes the pipeline."""

    model_config = ConfigDict(strict=True)

    module: str
    operation: str
    message: str
    detail: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def log(self) -> "ErrorModel":
        logger.error(
            "[%s.%s] %s | %s", self.module, self.operation, self.message, self.detail
        )
        return self


class _LineItemBase(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    item_code: str
    description: str
    category: str = ""
    quantity: Decimal
    billing_period_start: date
    billing_period_end: date
    # The source row exactly as parsed (pre-Decimal), preserved for the audit trail.
    raw_source: dict[str, str] = Field(default_factory=dict)

    @field_validator("quantity", mode="before")
    @classmethod
    def _v_quantity(cls, v: Any) -> Decimal:
        if isinstance(v, bool) or isinstance(v, float):
            raise ValueError("Quantity must arrive as a string, not a float")
        return Decimal(str(v).strip().replace(",", ""))

    @field_validator("billing_period_start", "billing_period_end", mode="before")
    @classmethod
    def _v_dates(cls, v: Any) -> date:
        return _to_date(v)

    @field_validator("item_code", "description", mode="before")
    @classmethod
    def _v_strip(cls, v: Any) -> str:
        return str(v).strip()


class ContractLineItem(_LineItemBase):
    """One line of what the contract says you should be charged."""

    agreed_rate: Decimal
    agreed_amount: Decimal
    cap_amount: Decimal | None = None
    contract_clause: str | None = None

    @field_validator("agreed_rate", "agreed_amount", "cap_amount", mode="before")
    @classmethod
    def _v_money(cls, v: Any) -> Decimal | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return to_money(v)


class InvoiceLineItem(_LineItemBase):
    """One line of what you were actually billed."""

    billed_rate: Decimal
    billed_amount: Decimal
    invoice_ref: str = ""

    @field_validator("billed_rate", "billed_amount", mode="before")
    @classmethod
    def _v_money(cls, v: Any) -> Decimal:
        return to_money(v)


class ToleranceProfile(BaseModel):
    """How much variance is acceptable before a line item is flagged.

    A variance is out of tolerance when it exceeds BOTH the absolute and the
    percentage threshold (avoids flagging a $2 blip on a tiny item, or a 0.001%
    drift on a huge one). DISPUTE severity kicks in past the review band.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    pct_tolerance: Decimal
    abs_tolerance: Decimal
    review_band_multiplier: Decimal = Decimal("3")

    @field_validator(
        "pct_tolerance", "abs_tolerance", "review_band_multiplier", mode="before"
    )
    @classmethod
    def _v_decimal(cls, v: Any) -> Decimal:
        if isinstance(v, bool) or isinstance(v, float):
            raise ValueError("Tolerance values must arrive as strings, not floats")
        return Decimal(str(v))


class AuditContext(BaseModel):
    """The case file: which use case, which documents, which rules."""

    model_config = ConfigDict(strict=True)

    audit_id: str
    use_case: UseCase
    contract_path: str
    invoice_path: str
    tolerance_profile: ToleranceProfile
    # Two-letter state code (e.g. "CA") or "DEFAULT"; drives the legal language.
    jurisdiction: str = "DEFAULT"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VarianceFinding(BaseModel):
    """One audited line: agreed vs. billed, exact delta, and its severity."""

    model_config = ConfigDict(strict=True, frozen=True)

    item_code: str
    description: str
    contract_clause: str | None = None
    invoice_ref: str = ""
    agreed_amount: Decimal
    billed_amount: Decimal
    variance_amount: Decimal
    variance_pct: Decimal | None = None
    cap_breached: bool = False
    severity: Severity
    explanation: str
    # Key of this finding's entry in AuditResult.audit_trail (set by the engine).
    trail_key: str | None = None


class AuditResult(BaseModel):
    """Everything the audit produced, including totals and any non-fatal errors."""

    model_config = ConfigDict(strict=True)

    audit_id: str
    use_case: UseCase
    findings: list[VarianceFinding] = Field(default_factory=list)
    unmatched_contract_items: list[ContractLineItem] = Field(default_factory=list)
    unmatched_invoice_items: list[InvoiceLineItem] = Field(default_factory=list)
    total_agreed: Decimal = Decimal("0.00")
    total_billed: Decimal = Decimal("0.00")
    total_recoverable: Decimal = Decimal("0.00")
    # Immutable lineage per finding: each entry is frozen once written by the engine.
    audit_trail: dict[str, AuditTrailEntry] = Field(default_factory=dict)
    errors: list[ErrorModel] = Field(default_factory=list)

    @property
    def dispute_findings(self) -> list[VarianceFinding]:
        return [f for f in self.findings if f.severity == Severity.DISPUTE]
