"""Shared ingestion contract: every parser returns an IngestResult, never raises.

Money survives the whole path as strings and is only converted to Decimal inside
the Pydantic validators — a float can never touch a financial value.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config.models import ContractLineItem, ErrorModel, InvoiceLineItem

logger = logging.getLogger(__name__)

LineItem = ContractLineItem | InvoiceLineItem


class IngestResult(BaseModel):
    """Outcome of parsing one document: validated rows plus per-row errors."""

    model_config = ConfigDict(strict=False)

    source_file: str
    line_items: list[LineItem] = Field(default_factory=list)
    errors: list[ErrorModel] = Field(default_factory=list)
    rows_parsed: int = 0
    rows_rejected: int = 0

    @property
    def fatal(self) -> bool:
        """True when the document produced no usable rows at all."""
        return self.rows_parsed == 0


class BaseIngestor(ABC):
    """Template for all document parsers. Subclasses only implement _read_rows."""

    module_name: str = "ingestion.base"

    @abstractmethod
    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        """Return raw rows as header->string-value dicts. May raise; ingest() catches."""

    def ingest(
        self,
        path: Path,
        column_map: dict[str, str],
        target_model: type[LineItem],
    ) -> IngestResult:
        result = IngestResult(source_file=str(path))
        try:
            raw_rows = self._read_rows(path)
        except Exception as exc:  # noqa: BLE001 — zero-exception boundary
            result.errors.append(
                ErrorModel(
                    module=self.module_name,
                    operation="ingest",
                    message=f"Failed to read {path.name}",
                    detail=f"{type(exc).__name__}: {exc}",
                ).log()
            )
            return result

        missing = [
            header for header in column_map if raw_rows and header not in raw_rows[0]
        ]
        if missing:
            result.errors.append(
                ErrorModel(
                    module=self.module_name,
                    operation="ingest",
                    message=f"{path.name} is missing expected columns",
                    detail=f"Missing: {', '.join(missing)}. Found: {', '.join(raw_rows[0])}",
                ).log()
            )

        for row_num, raw in enumerate(raw_rows, start=2):  # row 1 = headers
            mapped = {
                field: raw[header].strip()
                for header, field in column_map.items()
                if header in raw and raw[header].strip() != ""
            }
            if not mapped:
                continue  # fully blank row
            try:
                # raw_source preserves the pre-Decimal strings for the audit trail.
                result.line_items.append(
                    target_model.model_validate({**mapped, "raw_source": dict(mapped)})
                )
                result.rows_parsed += 1
            except ValidationError as exc:
                result.rows_rejected += 1
                result.errors.append(
                    ErrorModel(
                        module=self.module_name,
                        operation="validate_row",
                        message=f"{path.name} row {row_num} rejected",
                        detail="; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()
                        ),
                    ).log()
                )
        return result
