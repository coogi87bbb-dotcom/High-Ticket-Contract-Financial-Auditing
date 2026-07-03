"""VarianceCalculator: exact Decimal variance math and severity classification.

Severity rules (per ToleranceProfile):
- A variance is OUT of tolerance only when it exceeds BOTH the absolute and the
  percentage threshold.
- DISPUTE: an overcharge beyond the review band (tolerance x multiplier), or any
  breach of a contractual cap.
- REVIEW: out of tolerance but inside the review band, or any material undercharge
  (worth confirming, not disputing).

calculate_with_trail() additionally returns an immutable AuditTrailEntry spelling
out the literal formula evaluated, the raw pre-Decimal source values, the rule that
fired, and the match lineage — the evidence record behind the finding.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from analyzer.matcher import MatchedPair
from config.models import (
    CENTS,
    InvoiceLineItem,
    Severity,
    ToleranceProfile,
    VarianceFinding,
)
from config.schemas import AuditTrailEntry, MatchMethod

_PCT_PLACES = Decimal("0.01")
_ONE = Decimal("1")


class VarianceCalculator:
    def __init__(self, tolerance: ToleranceProfile) -> None:
        self.tolerance = tolerance

    def _exceeds(
        self, variance: Decimal, pct: Decimal | None, multiplier: Decimal
    ) -> bool:
        abs_limit = self.tolerance.abs_tolerance * multiplier
        pct_limit = self.tolerance.pct_tolerance * multiplier
        if abs(variance) <= abs_limit:
            return False
        return pct is None or abs(pct) > pct_limit

    def calculate(self, pair: MatchedPair) -> VarianceFinding:
        finding, _ = self.calculate_with_trail(pair)
        return finding

    def calculate_with_trail(
        self, pair: MatchedPair
    ) -> tuple[VarianceFinding, AuditTrailEntry]:
        agreed = pair.contract.agreed_amount
        billed = pair.invoice.billed_amount
        variance = (billed - agreed).quantize(CENTS, rounding=ROUND_HALF_UP)

        pct: Decimal | None = None
        pct_formula: str | None = None
        if agreed != 0:
            pct = (variance / agreed * 100).quantize(
                _PCT_PLACES, rounding=ROUND_HALF_UP
            )
            pct_formula = (
                f"variance_pct = (variance_amount / agreed_amount) * 100 = "
                f"({variance} / {agreed}) * 100 = {pct}"
            )

        cap = pair.contract.cap_amount
        cap_breached = cap is not None and billed > cap

        mult = self.tolerance.review_band_multiplier
        out_of_tolerance = self._exceeds(variance, pct, _ONE)
        dispute_grade = variance > 0 and self._exceeds(variance, pct, mult)

        if cap_breached:
            severity = Severity.DISPUTE
            overage = (billed - cap).quantize(CENTS, rounding=ROUND_HALF_UP)  # type: ignore[operator]
            explanation = (
                f"Billed {billed} exceeds the contractual cap of {cap} by {overage}."
            )
            severity_rule = (
                f"RULE cap_breach: billed_amount {billed} > cap_amount {cap} "
                f"(overage {overage}) -> DISPUTE"
            )
        elif dispute_grade:
            severity = Severity.DISPUTE
            explanation = (
                f"Overcharge of {variance} ({pct}% above the agreed {agreed}) is far "
                f"beyond the allowed tolerance."
            )
            severity_rule = (
                f"RULE dispute_band: |variance| {abs(variance)} > "
                f"abs_tolerance*{mult} {self.tolerance.abs_tolerance * mult} AND "
                f"|pct| {abs(pct) if pct is not None else 'n/a'} > "
                f"pct_tolerance*{mult} {self.tolerance.pct_tolerance * mult} -> DISPUTE"
            )
        elif out_of_tolerance:
            severity = Severity.REVIEW
            direction = "Overcharge" if variance > 0 else "Undercharge"
            explanation = (
                f"{direction} of {abs(variance)} versus the agreed {agreed} exceeds "
                f"tolerance and should be reviewed."
            )
            severity_rule = (
                f"RULE review_band: |variance| {abs(variance)} > "
                f"abs_tolerance {self.tolerance.abs_tolerance} but not beyond the "
                f"{mult}x review band -> REVIEW"
            )
        else:
            severity = Severity.WITHIN_TOLERANCE
            explanation = "Billed amount matches the contract within tolerance."
            severity_rule = (
                f"RULE within_tolerance: |variance| {abs(variance)} <= "
                f"abs_tolerance {self.tolerance.abs_tolerance} OR |pct| within "
                f"pct_tolerance {self.tolerance.pct_tolerance} -> WITHIN_TOLERANCE"
            )

        finding = VarianceFinding(
            item_code=pair.contract.item_code,
            description=pair.contract.description,
            contract_clause=pair.contract.contract_clause,
            invoice_ref=pair.invoice.invoice_ref,
            agreed_amount=agreed,
            billed_amount=billed,
            variance_amount=variance,
            variance_pct=pct,
            cap_breached=cap_breached,
            severity=severity,
            explanation=explanation,
        )
        trail = AuditTrailEntry(
            item_code=pair.contract.item_code,
            invoice_ref=pair.invoice.invoice_ref,
            match_method=pair.method,
            match_confidence=pair.confidence,
            matched_on=pair.matched_on,
            raw_contract_row=dict(pair.contract.raw_source),
            raw_invoice_row=dict(pair.invoice.raw_source),
            variance_formula=(
                f"variance_amount = billed_amount - agreed_amount = "
                f"{billed} - {agreed} = {variance}"
            ),
            pct_formula=pct_formula,
            severity_rule=severity_rule,
            tolerance_applied={
                "abs_tolerance": str(self.tolerance.abs_tolerance),
                "pct_tolerance": str(self.tolerance.pct_tolerance),
                "review_band_multiplier": str(mult),
            },
            contract_clause=pair.contract.contract_clause,
        )
        return finding, trail

    def flag_duplicate(
        self,
        pair: MatchedPair,
        duplicate: InvoiceLineItem,
        aggregate_billed: Decimal,
        total_lines: int,
    ) -> tuple[VarianceFinding, AuditTrailEntry]:
        """Flag a re-billed code (duplicate/unbundled charge) as dispute-grade.

        `pair` is the line that legitimately matched the contract; `duplicate` is an
        additional invoice line under the same code with no separate contractual
        basis, so its full amount is recoverable. If the contract line carries a cap,
        the AGGREGATE billed across all lines under the code is tested against it.
        """
        billed = duplicate.billed_amount
        cap = pair.contract.cap_amount
        cap_breached = cap is not None and aggregate_billed > cap

        explanation = (
            f"Duplicate/unbundled billing: code {duplicate.item_code} was already "
            f"billed and matched to the fee schedule on this statement; this "
            f"additional line of {billed} has no separate contractual basis."
        )
        if cap_breached:
            overage = (aggregate_billed - cap).quantize(CENTS, rounding=ROUND_HALF_UP)  # type: ignore[operator]
            explanation += (
                f" Aggregate billed under this code is {aggregate_billed}, exceeding "
                f"the global cap of {cap} by {overage}."
            )
            severity_rule = (
                f"RULE duplicate_unbundled: {total_lines} lines billed under code "
                f"{duplicate.item_code}; aggregate {aggregate_billed} > global cap "
                f"{cap} (overage {overage}) -> DISPUTE"
            )
        else:
            severity_rule = (
                f"RULE duplicate_unbundled: {total_lines} lines billed under code "
                f"{duplicate.item_code} but only one contracted line exists "
                f"(aggregate {aggregate_billed}) -> DISPUTE"
            )

        finding = VarianceFinding(
            item_code=duplicate.item_code,
            description=duplicate.description,
            contract_clause=pair.contract.contract_clause,
            invoice_ref=duplicate.invoice_ref,
            agreed_amount=Decimal("0.00"),
            billed_amount=billed,
            variance_amount=billed,
            variance_pct=None,
            cap_breached=cap_breached,
            severity=Severity.DISPUTE,
            explanation=explanation,
        )
        trail = AuditTrailEntry(
            item_code=duplicate.item_code,
            invoice_ref=duplicate.invoice_ref,
            match_method=MatchMethod.DUPLICATE_KEY,
            match_confidence=Decimal("100.00"),
            matched_on=(
                f"item_code '{duplicate.item_code}' duplicates a fee-schedule line "
                f"already matched on this statement ({total_lines} lines total)"
            ),
            raw_contract_row=dict(pair.contract.raw_source),
            raw_invoice_row=dict(duplicate.raw_source),
            variance_formula=(
                f"variance_amount = billed_amount (no additional contractual "
                f"entitlement) = {billed}"
            ),
            pct_formula=None,
            severity_rule=severity_rule,
            tolerance_applied={
                "abs_tolerance": str(self.tolerance.abs_tolerance),
                "pct_tolerance": str(self.tolerance.pct_tolerance),
                "review_band_multiplier": str(self.tolerance.review_band_multiplier),
            },
            contract_clause=pair.contract.contract_clause,
        )
        return finding, trail
