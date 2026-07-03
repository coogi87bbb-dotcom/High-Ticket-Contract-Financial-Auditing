"""LineItemMatcher: pairs each contract line with its invoice counterpart.

Two-pass strategy, every pair stamped with its match lineage:

1. EXACT_KEY — exact match on the configured keys (default item_code), case- and
   whitespace-insensitive. Confidence is always 100.00.
2. FUZZY_DESCRIPTION — token-sort similarity between descriptions (word order and
   case ignored), accepted only when the score meets the configurable threshold
   AND the billing periods overlap. Confidence is the similarity score itself.

The similarity algorithm is difflib's SequenceMatcher over token-sorted strings —
fully deterministic with zero external dependencies, so a score quoted in a dispute
letter can always be reproduced. Scores are similarity metrics (not money) and are
quantized to two places via exact string conversion.

Anything left over is reported as unmatched — an unmatched invoice line is itself
a red flag (a charge with no contractual basis).
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher

from pydantic import BaseModel, ConfigDict

from config.models import ContractLineItem, InvoiceLineItem
from config.schemas import MatchMethod

FULL_CONFIDENCE = Decimal("100.00")
DEFAULT_FUZZY_THRESHOLD = Decimal("85.00")
_SCORE_PLACES = Decimal("0.01")


class MatchedPair(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    contract: ContractLineItem
    invoice: InvoiceLineItem
    method: MatchMethod = MatchMethod.EXACT_KEY
    confidence: Decimal = FULL_CONFIDENCE
    matched_on: str = "item_code (exact)"


class MatchResult(BaseModel):
    model_config = ConfigDict(strict=True)

    pairs: list[MatchedPair]
    unmatched_contract: list[ContractLineItem]
    unmatched_invoice: list[InvoiceLineItem]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def _token_sort(value: str) -> str:
    return " ".join(sorted(re.findall(r"[A-Z0-9]+", _normalize(value))))


def token_sort_ratio(a: str, b: str) -> Decimal:
    """Similarity score 0.00–100.00 between two strings, word order ignored."""
    ratio = SequenceMatcher(None, _token_sort(a), _token_sort(b)).ratio()
    return (Decimal(str(ratio)) * 100).quantize(_SCORE_PLACES, rounding=ROUND_HALF_UP)


class LineItemMatcher:
    def __init__(
        self,
        match_keys: list[str] | None = None,
        fuzzy_threshold: Decimal = DEFAULT_FUZZY_THRESHOLD,
    ) -> None:
        self.match_keys = match_keys or ["item_code"]
        self.fuzzy_threshold = fuzzy_threshold

    def _key(self, item: ContractLineItem | InvoiceLineItem) -> tuple[str, ...]:
        return tuple(_normalize(str(getattr(item, k, ""))) for k in self.match_keys)

    @staticmethod
    def _periods_overlap(c: ContractLineItem, i: InvoiceLineItem) -> bool:
        return (
            c.billing_period_start <= i.billing_period_end
            and i.billing_period_start <= c.billing_period_end
        )

    def _best_fuzzy_hit(
        self, c_item: ContractLineItem, candidates: list[InvoiceLineItem]
    ) -> tuple[InvoiceLineItem, Decimal] | None:
        best: tuple[InvoiceLineItem, Decimal] | None = None
        for i_item in candidates:
            if not self._periods_overlap(c_item, i_item):
                continue
            score = token_sort_ratio(c_item.description, i_item.description)
            if score < self.fuzzy_threshold:
                continue
            if best is None or score > best[1]:
                best = (i_item, score)
        return best

    def match(
        self,
        contract_items: list[ContractLineItem],
        invoice_items: list[InvoiceLineItem],
    ) -> MatchResult:
        pairs: list[MatchedPair] = []
        remaining_invoice = list(invoice_items)
        unmatched_contract: list[ContractLineItem] = []

        # Pass 1: exact key match — confidence 100.00 by definition
        for c_item in contract_items:
            key = self._key(c_item)
            hit = next((i for i in remaining_invoice if self._key(i) == key), None)
            if hit is not None:
                pairs.append(
                    MatchedPair(
                        contract=c_item,
                        invoice=hit,
                        method=MatchMethod.EXACT_KEY,
                        confidence=FULL_CONFIDENCE,
                        matched_on=f"{' + '.join(self.match_keys)} (exact)",
                    )
                )
                remaining_invoice.remove(hit)
            else:
                unmatched_contract.append(c_item)

        # Pass 2: fuzzy description + overlapping billing period
        still_unmatched: list[ContractLineItem] = []
        for c_item in unmatched_contract:
            best = self._best_fuzzy_hit(c_item, remaining_invoice)
            if best is not None:
                i_item, score = best
                pairs.append(
                    MatchedPair(
                        contract=c_item,
                        invoice=i_item,
                        method=MatchMethod.FUZZY_DESCRIPTION,
                        confidence=score,
                        matched_on=(
                            f"description token-sort similarity {score}% "
                            f"(threshold {self.fuzzy_threshold}%): "
                            f"'{c_item.description}' ~ '{i_item.description}'"
                        ),
                    )
                )
                remaining_invoice.remove(i_item)
            else:
                still_unmatched.append(c_item)

        return MatchResult(
            pairs=pairs,
            unmatched_contract=still_unmatched,
            unmatched_invoice=remaining_invoice,
        )
