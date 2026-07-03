"""Ingestion guarantees: bad rows become ErrorModels, never exceptions."""

from decimal import Decimal
from pathlib import Path

from config.models import ContractLineItem, ErrorModel
from config.tolerances import load_use_case_profile
from ingestion.csv_ingestor import CsvIngestor
from ingestion.factory import IngestorFactory

PROFILE = load_use_case_profile("lease_cam")

HEADER = (
    "Item Code,Expense Category,Description,Agreed Rate,Quantity,"
    "Agreed Amount,Annual Cap,Period Start,Period End,Lease Clause"
)
GOOD_ROW = "CAM-001,Grounds,Landscaping,1000.00,1,1000.00,,2026-01-01,2026-12-31,4.2(a)"
BAD_ROW = (
    "CAM-002,Admin,Admin Fee,not-money,1,also-not-money,,2026-01-01,2026-12-31,4.3"
)


def _write_csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "contract.csv"
    path.write_text("\n".join([HEADER, *rows]), encoding="utf-8")
    return path


class TestCsvIngestor:
    def test_happy_path(self, tmp_path):
        path = _write_csv(tmp_path, GOOD_ROW)
        result = CsvIngestor().ingest(path, PROFILE.contract_columns, ContractLineItem)
        assert result.rows_parsed == 1
        assert result.errors == []
        assert result.line_items[0].agreed_amount == Decimal("1000.00")

    def test_bad_row_becomes_error_not_exception(self, tmp_path):
        path = _write_csv(tmp_path, GOOD_ROW, BAD_ROW)
        result = CsvIngestor().ingest(path, PROFILE.contract_columns, ContractLineItem)
        assert result.rows_parsed == 1
        assert result.rows_rejected == 1
        assert len(result.errors) == 1
        assert "row 3" in result.errors[0].message

    def test_unreadable_file_returns_clean_error(self, tmp_path):
        result = CsvIngestor().ingest(
            tmp_path / "ghost.csv", PROFILE.contract_columns, ContractLineItem
        )
        assert result.fatal
        assert len(result.errors) == 1


class TestFactory:
    def test_routes_csv(self, tmp_path):
        path = _write_csv(tmp_path, GOOD_ROW)
        assert isinstance(IngestorFactory.for_file(path), CsvIngestor)

    def test_unsupported_type_returns_error_model(self, tmp_path):
        path = tmp_path / "contract.docx"
        path.write_text("hello")
        outcome = IngestorFactory.for_file(path)
        assert isinstance(outcome, ErrorModel)
        assert "Unsupported file type" in outcome.message

    def test_missing_file_returns_error_model(self):
        outcome = IngestorFactory.for_file("does/not/exist.csv")
        assert isinstance(outcome, ErrorModel)
        assert "not found" in outcome.message
