"""Upgrade guarantees: fuzzy matching lineage and the immutable audit trail."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from analyzer.matcher import LineItemMatcher, MatchedPair, token_sort_ratio
from analyzer.variance import VarianceCalculator
from config.models import ToleranceProfile
from config.schemas import MatchMethod
from tests.helpers import contract_item, invoice_item

CALC = VarianceCalculator(
    ToleranceProfile(
        pct_tolerance="0.50", abs_tolerance="5.00", review_band_multiplier="3"
    )
)


class TestFuzzyMatching:
    def test_token_sort_ignores_word_order(self):
        assert token_sort_ratio("Removal Snow", "Snow Removal") == Decimal("100.00")

    def test_abbreviated_description_matches_above_threshold(self):
        result = LineItemMatcher(fuzzy_threshold=Decimal("85")).match(
            [contract_item(item_code="OLD-1", description="Landscaping Services")],
            [invoice_item(item_code="NEW-1", description="Landscaping Service")],
        )
        assert len(result.pairs) == 1
        pair = result.pairs[0]
        assert pair.method == MatchMethod.FUZZY_DESCRIPTION
        assert Decimal("85") <= pair.confidence < Decimal("100")

    def test_dissimilar_descriptions_stay_unmatched(self):
        result = LineItemMatcher(fuzzy_threshold=Decimal("85")).match(
            [contract_item(item_code="A-1", description="Window Cleaning")],
            [
                invoice_item(
                    item_code="B-1", description="Capital Improvement Surcharge"
                )
            ],
        )
        assert not result.pairs
        assert len(result.unmatched_contract) == 1
        assert len(result.unmatched_invoice) == 1

    def test_threshold_is_configurable(self):
        loose = LineItemMatcher(fuzzy_threshold=Decimal("40")).match(
            [contract_item(item_code="A-1", description="Security Patrol Services")],
            [invoice_item(item_code="B-1", description="Security Svcs")],
        )
        strict = LineItemMatcher(fuzzy_threshold=Decimal("99")).match(
            [contract_item(item_code="A-1", description="Security Patrol Services")],
            [invoice_item(item_code="B-1", description="Security Svcs")],
        )
        assert len(loose.pairs) == 1
        assert not strict.pairs

    def test_exact_key_match_wins_with_full_confidence(self):
        result = LineItemMatcher().match(
            [contract_item(item_code="CAM-002")], [invoice_item(item_code="CAM-002")]
        )
        assert result.pairs[0].method == MatchMethod.EXACT_KEY
        assert result.pairs[0].confidence == Decimal("100.00")


class TestAuditTrail:
    def _trail(self):
        contract = contract_item(
            agreed_amount="1000.00", raw_source={"Agreed Amount": "1,000.00"}
        )
        invoice = invoice_item(
            billed_amount="1250.00", raw_source={"Billed Amount": "$1,250.00"}
        )
        _, trail = CALC.calculate_with_trail(
            MatchedPair(contract=contract, invoice=invoice)
        )
        return trail

    def test_formula_is_spelled_out_exactly(self):
        trail = self._trail()
        assert (
            trail.variance_formula
            == "variance_amount = billed_amount - agreed_amount = 1250.00 - 1000.00 = 250.00"
        )
        assert "25.00" in trail.pct_formula

    def test_raw_pre_decimal_values_are_preserved(self):
        trail = self._trail()
        assert trail.raw_contract_row == {"Agreed Amount": "1,000.00"}
        assert trail.raw_invoice_row == {"Billed Amount": "$1,250.00"}

    def test_severity_rule_and_tolerances_are_recorded(self):
        trail = self._trail()
        assert trail.severity_rule.startswith("RULE dispute_band")
        assert trail.tolerance_applied == {
            "abs_tolerance": "5.00",
            "pct_tolerance": "0.50",
            "review_band_multiplier": "3",
        }

    def test_contract_clause_is_linked(self):
        assert self._trail().contract_clause == "Section 4.3"

    def test_trail_entry_is_immutable(self):
        trail = self._trail()
        with pytest.raises(ValidationError):
            trail.variance_formula = "tampered"

    def test_cap_breach_rule_is_recorded(self):
        _, trail = CALC.calculate_with_trail(
            MatchedPair(
                contract=contract_item(agreed_amount="2400.00", cap_amount="2500.00"),
                invoice=invoice_item(billed_amount="2600.00"),
            )
        )
        assert trail.severity_rule.startswith("RULE cap_breach")
        assert "2500.00" in trail.severity_rule
