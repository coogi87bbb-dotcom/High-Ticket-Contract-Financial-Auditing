"""Excel (.xlsx/.xls) parser. dtype=str keeps money as text so Decimal conversion stays exact."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.base import BaseIngestor


class ExcelIngestor(BaseIngestor):
    module_name = "ingestion.excel"

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        frame = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
        frame.columns = [str(c).strip() for c in frame.columns]
        return frame.to_dict(orient="records")
