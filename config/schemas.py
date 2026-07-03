"""Audit-trail schemas: the immutable evidence record behind every finding.

Each AuditTrailEntry captures the full lineage of one matched line item — how the
match was made (and with what confidence), the raw source values exactly as they
appeared in the documents BEFORE Decimal conversion, the literal formula evaluated,
the tolerance rule that fired, and the contract section involved. Entries are
frozen: once written by the engine they cannot be altered, which is what makes the
audit defensible.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MatchMethod(StrEnum):
    EXACT_KEY = "EXACT_KEY"  # matched on the configured key(s), e.g. item_code
    FUZZY_DESCRIPTION = "FUZZY_DESCRIPTION"  # token-sort similarity above threshold
    DUPLICATE_KEY = "DUPLICATE_KEY"  # re-billing of a code already matched (unbundling)


class AuditTrailEntry(BaseModel):
    """Immutable lineage record for one audited line item."""

    model_config = ConfigDict(strict=True, frozen=True)

    item_code: str
    invoice_ref: str = ""

    # --- Match lineage ---
    match_method: MatchMethod
    match_confidence: Decimal  # 0.00–100.00; 100.00 for exact key matches
    matched_on: str  # human-readable description of what was compared

    # --- Raw values exactly as parsed from the source documents (pre-Decimal) ---
    raw_contract_row: dict[str, str] = Field(default_factory=dict)
    raw_invoice_row: dict[str, str] = Field(default_factory=dict)

    # --- The mathematics, spelled out ---
    variance_formula: (
        str  # e.g. "variance = billed − agreed = 1250.00 − 1000.00 = 250.00"
    )
    pct_formula: str | None = None
    severity_rule: str  # the exact tolerance rule that fired, with its numbers
    tolerance_applied: dict[str, str] = Field(default_factory=dict)

    # --- Contract linkage ---
    contract_clause: str | None = None
