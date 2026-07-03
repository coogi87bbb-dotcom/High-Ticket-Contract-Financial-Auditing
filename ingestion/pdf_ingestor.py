"""PDF parser: extracts tables from every page via pdfplumber.

Assumes the first table row on the first page holds the column headers; later
pages may repeat or omit the header row (both are handled).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from ingestion.base import BaseIngestor


class PdfIngestor(BaseIngestor):
    module_name = "ingestion.pdf"

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        headers: list[str] | None = None
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for raw_row in table:
                        cells = [(c or "").strip() for c in raw_row]
                        if not any(cells):
                            continue
                        if headers is None:
                            headers = cells
                            continue
                        if cells == headers:
                            continue  # repeated header on a later page
                        rows.append(dict(zip(headers, cells)))
        if headers is None:
            raise ValueError("No tables found in PDF — is this a scanned image?")
        return rows
