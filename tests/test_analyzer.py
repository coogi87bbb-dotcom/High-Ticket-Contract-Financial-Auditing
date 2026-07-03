"""Analyzer guarantees: exact variance math and correct severity classification."""

from decimal import Decimal

from analyzer.matcher import LineItemMatcher, MatchedPair
from analyzer.variance import VarianceCalculator
from config.models import Severity, ToleranceProfile
from tests.helpers import contract_item, invoice_item

TOLERANCE = ToleranceProfile(
    pct_tolerance="0.50", abs_tolerance="5.00", review_band_multiplier="3"
)


def _finding(contract, invoice):
    return VarianceCalculator(TOLERANCE).calculate(
        MatchedPair(contract=contract, invoice=invoice)
    )


class TestVarianceMath:
    def test_exact_overcharge_and_pct(self):
        f = _finding(
            contract_item(agreed_amount="1000.00"),
            invoice_item(billed_amount="1250.00"),
        )
        assert f.variance_amount == Decimal("250.00")
        assert f.variance_pct == Decimal("25.00")
        assert f.severity == Severity.DISPUTE

    def test_penny_variance_is_within_tolerance(self):
        f = _finding(
            contract_item(agreed_amount="100.00"), invoice_item(billed_amount="100.01")
        )
        assert f.variance_amount == Decimal("0.01")
        assert f.severity == Severity.WITHIN_TOLERANCE

    def test_review_band_between_tolerance_and_dispute(self):
        # $12 on $800 = 1.5%: past tolerance (5.00 / 0.5%) but inside 3x band (15.00 / 1.5%)
        f = _finding(
            contract_item(agreed_amount="800.00"), invoice_item(billed_amount="812.00")
        )
        assert f.severity == Severity.REVIEW

    def test_cap_breach_is_always_dispute(self):
        f = _finding(
            contract_item(agreed_amount="2400.00", cap_amount="2500.00"),
            invoice_item(billed_amount="2600.00"),
        )
        assert f.cap_breached
        assert f.severity == Severity.DISPUTE
        assert "cap" in f.explanation.lower()

    def test_undercharge_flags_review_not_dispute(self):
        f = _finding(
            contract_item(agreed_amount="1000.00"), invoice_item(billed_amount="900.00")
        )
        assert f.variance_amount == Decimal("-100.00")
        assert f.severity == Severity.REVIEW

    def test_zero_agreed_amount_does_not_divide_by_zero(self):
        f = _finding(
            contract_item(agreed_amount="0.00", agreed_rate="0.00"),
            invoice_item(billed_amount="10.00"),
        )
        assert f.variance_pct is None
        assert f.severity == Severity.REVIEW


class TestMatcher:
    def test_matches_on_item_code_case_insensitively(self):
        result = LineItemMatcher().match(
            [contract_item(item_code="cam-002 ")], [invoice_item(item_code="CAM-002")]
        )
        assert len(result.pairs) == 1
        assert not result.unmatched_contract
        assert not result.unmatched_invoice

    def test_fallback_matches_on_description_and_period(self):
        result = LineItemMatcher().match(
            [contract_item(item_code="LEGACY-1", description="Snow  Removal")],
            [invoice_item(item_code="NEW-9", description="snow removal")],
        )
        assert len(result.pairs) == 1

    def test_unmatched_items_are_reported_on_both_sides(self):
        result = LineItemMatcher().match(
            [contract_item(item_code="CAM-010", description="Window Cleaning")],
            [invoice_item(item_code="CAM-099", description="Capital Surcharge")],
        )
        assert not result.pairs
        assert len(result.unmatched_contract) == 1
        assert len(result.unmatched_invoice) == 1
