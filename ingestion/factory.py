"""IngestorFactory: routes each document to the right parser by file extension."""

from __future__ import annotations

from pathlib import Path

from config.models import ErrorModel
from ingestion.base import BaseIngestor
from ingestion.csv_ingestor import CsvIngestor
from ingestion.excel_ingestor import ExcelIngestor
from ingestion.pdf_ingestor import PdfIngestor

_REGISTRY: dict[str, type[BaseIngestor]] = {
    ".csv": CsvIngestor,
    ".xlsx": ExcelIngestor,
    ".xls": ExcelIngestor,
    ".pdf": PdfIngestor,
}


class IngestorFactory:
    @staticmethod
    def supported_types() -> list[str]:
        return sorted(_REGISTRY)

    @staticmethod
    def for_file(path: Path | str) -> BaseIngestor | ErrorModel:
        path = Path(path)
        if not path.is_file():
            return ErrorModel(
                module="ingestion.factory",
                operation="for_file",
                message=f"File not found: {path}",
                detail="Check the path — the file does not exist or is not readable.",
            ).log()
        ingestor_cls = _REGISTRY.get(path.suffix.lower())
        if ingestor_cls is None:
            return ErrorModel(
                module="ingestion.factory",
                operation="for_file",
                message=f"Unsupported file type '{path.suffix}' for {path.name}",
                detail=f"Supported types: {', '.join(IngestorFactory.supported_types())}",
            ).log()
        return ingestor_cls()
