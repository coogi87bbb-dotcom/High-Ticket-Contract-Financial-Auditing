"""CSV parser. dtype=str keeps money as text so Decimal conversion stays exact."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.base import BaseIngestor


class CsvIngestor(BaseIngestor):
    module_name = "ingestion.csv"

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        frame = pd.read_csv(path, dtype=str).fillna("")
        frame.columns = [str(c).strip() for c in frame.columns]
        return frame.to_dict(orient="records")
