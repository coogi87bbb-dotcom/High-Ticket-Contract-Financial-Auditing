"""Shared builders for test line items."""

from __future__ import annotations

from typing import Any

from config.models import ContractLineItem, InvoiceLineItem


def contract_item(**overrides: Any) -> ContractLineItem:
    base: dict[str, Any] = {
        "item_code": "CAM-002",
        "description": "Property Management Admin Fee",
        "category": "Administration",
        "quantity": "1",
        "agreed_rate": "1000.00",
        "agreed_amount": "1000.00",
        "billing_period_start": "2026-01-01",
        "billing_period_end": "2026-12-31",
        "contract_clause": "Section 4.3",
    }
    base.update(overrides)
    return ContractLineItem.model_validate(base)


def invoice_item(**overrides: Any) -> InvoiceLineItem:
    base: dict[str, Any] = {
        "item_code": "CAM-002",
        "description": "Property Management Admin Fee",
        "category": "Administration",
        "quantity": "1",
        "billed_rate": "1250.00",
        "billed_amount": "1250.00",
        "billing_period_start": "2026-01-01",
        "billing_period_end": "2026-12-31",
        "invoice_ref": "INV-1",
    }
    base.update(overrides)
    return InvoiceLineItem.model_validate(base)
