"""AuditEngine: orchestrates ingest -> match -> variance -> aggregate.

Every stage is wrapped so failures surface as ErrorModel entries on the
AuditResult; the engine itself never raises.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from analyzer.matcher import LineItemMatcher
from analyzer.variance import VarianceCalculator
from config.models import (
    CENTS,
    AuditContext,
    AuditResult,
    ContractLineItem,
    ErrorModel,
    InvoiceLineItem,
    Severity,
    VarianceFinding,
)
from config.tolerances import UseCaseProfile
from ingestion.base import IngestResult, LineItem
from ingestion.factory import IngestorFactory

logger = logging.getLogger(__name__)


class AuditEngine:
    def __init__(self, profile: UseCaseProfile) -> None:
        self.profile = profile

    def _ingest(
        self,
        path_str: str,
        column_map: dict[str, str],
        target_model: type[LineItem],
        result: AuditResult,
    ) -> IngestResult | None:
        """Parse one document; returns None (with errors recorded) when unusable."""
        ingestor = IngestorFactory.for_file(Path(path_str))
        if isinstance(ingestor, ErrorModel):
            result.errors.append(ingestor)
            return None
        ingest_result = ingestor.ingest(Path(path_str), column_map, target_model)
        result.errors.extend(ingest_result.errors)
        if ingest_result.fatal:
            result.errors.append(
                ErrorModel(
                    module="analyzer.engine",
                    operation="ingest",
                    message=f"No usable rows in {Path(path_str).name}",
                    detail="Every row failed validation or the document was empty.",
                ).log()
            )
            return None
        return ingest_result

    def run(self, context: AuditContext) -> AuditResult:
        result = AuditResult(audit_id=context.audit_id, use_case=context.use_case)
        try:
            contract = self._ingest(
                context.contract_path,
                self.profile.contract_columns,
                ContractLineItem,
                result,
            )
            invoice = self._ingest(
                context.invoice_path,
                self.profile.invoice_columns,
                InvoiceLineItem,
                result,
            )
            if contract is None or invoice is None:
                return result

            contract_items = [
                i for i in contract.line_items if isinstance(i, ContractLineItem)
            ]
            invoice_items = [
                i for i in invoice.line_items if isinstance(i, InvoiceLineItem)
            ]

            matcher = LineItemMatcher(
                self.profile.match_keys, fuzzy_threshold=self.profile.fuzzy_threshold
            )
            matched = matcher.match(contract_items, invoice_items)
            result.unmatched_contract_items = matched.unmatched_contract

            # Pass 3: duplicate/unbundled billing — unmatched invoice lines that
            # re-use a code already matched to the contract have no separate
            # contractual basis; group them so aggregates can be tested vs caps.
            pair_by_key: dict[tuple[str, ...], object] = {}
            for pair in matched.pairs:
                pair_by_key.setdefault(matcher.key_for(pair.contract), pair)
            duplicate_groups: dict[tuple[str, ...], list[InvoiceLineItem]] = {}
            leftover_invoice: list[InvoiceLineItem] = []
            for item in matched.unmatched_invoice:
                item_key = matcher.key_for(item)
                if item_key in pair_by_key:
                    duplicate_groups.setdefault(item_key, []).append(item)
                else:
                    leftover_invoice.append(item)
            result.unmatched_invoice_items = leftover_invoice

            calculator = VarianceCalculator(context.tolerance_profile)

            def store(finding: VarianceFinding, trail_entry: object) -> None:
                # Collision-safe key: duplicate item codes get #2, #3, ... suffixes.
                key, n = finding.item_code, 2
                while key in result.audit_trail:
                    key = f"{finding.item_code}#{n}"
                    n += 1
                result.findings.append(finding.model_copy(update={"trail_key": key}))
                result.audit_trail[key] = trail_entry  # type: ignore[assignment]

            for pair in matched.pairs:
                store(*calculator.calculate_with_trail(pair))
            for item_key, dupes in duplicate_groups.items():
                pair = pair_by_key[item_key]
                aggregate = (
                    pair.invoice.billed_amount  # type: ignore[attr-defined]
                    + sum((d.billed_amount for d in dupes), Decimal("0"))
                ).quantize(CENTS, rounding=ROUND_HALF_UP)
                for dup in dupes:
                    store(
                        *calculator.flag_duplicate(
                            pair,  # type: ignore[arg-type]
                            dup,
                            aggregate_billed=aggregate,
                            total_lines=len(dupes) + 1,
                        )
                    )

            result.total_agreed = sum(
                (i.agreed_amount for i in contract_items), Decimal("0")
            ).quantize(CENTS, rounding=ROUND_HALF_UP)
            result.total_billed = sum(
                (i.billed_amount for i in invoice_items), Decimal("0")
            ).quantize(CENTS, rounding=ROUND_HALF_UP)
            result.total_recoverable = sum(
                (
                    f.variance_amount
                    for f in result.findings
                    if f.severity == Severity.DISPUTE and f.variance_amount > 0
                ),
                Decimal("0"),
            ).quantize(CENTS, rounding=ROUND_HALF_UP)
        except Exception as exc:  # noqa: BLE001 — zero-exception boundary
            result.errors.append(
                ErrorModel(
                    module="analyzer.engine",
                    operation="run",
                    message="Audit engine failed unexpectedly",
                    detail=f"{type(exc).__name__}: {exc}",
                ).log()
            )
        return result
