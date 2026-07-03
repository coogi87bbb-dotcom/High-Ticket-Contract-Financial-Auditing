"""Model guarantees: exact Decimal money, float rejection, strict validation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from config.models import to_money
from tests.helpers import contract_item, invoice_item


class TestToMoney:
    def test_parses_plain_string(self):
        assert to_money("1234.56") == Decimal("1234.56")

    def test_parses_currency_formatting(self):
        assert to_money("$1,234.56") == Decimal("1234.56")

    def test_parses_accounting_negatives(self):
        assert to_money("(50.00)") == Decimal("-50.00")

    def test_quantizes_to_cents_half_up(self):
        assert to_money("1.005") == Decimal("1.01")

    def test_rejects_float(self):
        with pytest.raises(ValueError, match="Float"):
            to_money(10.5)

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            to_money("twelve dollars")


class TestLineItems:
    def test_contract_item_from_strings(self):
        item = contract_item()
        assert item.agreed_amount == Decimal("1000.00")
        assert item.billing_period_start.year == 2026

    def test_float_money_rejected_at_model_boundary(self):
        with pytest.raises(ValidationError):
            contract_item(agreed_amount=1000.50)

    def test_missing_cap_becomes_none(self):
        assert contract_item().cap_amount is None

    def test_cap_parses_when_present(self):
        assert contract_item(cap_amount="2,500.00").cap_amount == Decimal("2500.00")

    def test_invoice_item_money_is_exact(self):
        assert invoice_item(billed_amount="0.10").billed_amount + invoice_item(
            billed_amount="0.20"
        ).billed_amount == Decimal("0.30")  # the classic float bug, impossible here

    def test_frozen_items_cannot_be_mutated(self):
        with pytest.raises(ValidationError):
            contract_item().item_code = "TAMPERED"
