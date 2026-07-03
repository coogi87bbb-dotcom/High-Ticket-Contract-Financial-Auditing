"""Reporter guarantees: exact amounts, dispute-only content, jurisdiction-aware law."""

from decimal import Decimal

from analyzer.matcher import MatchedPair
from analyzer.variance import VarianceCalculator
from config.models import AuditResult, Severity, ToleranceProfile, UseCase
from config.tolerances import load_use_case_profile
from reporter.generator import LegalClauseRegistry, RegionalDisputeGenerator
from reporter.summary_reporter import SummaryReporter
from tests.helpers import contract_item, invoice_item

PROFILE = load_use_case_profile("lease_cam")
CALC = VarianceCalculator(
    ToleranceProfile(
        pct_tolerance="0.50", abs_tolerance="5.00", review_band_multiplier="3"
    )
)


def _result() -> AuditResult:
    dispute, dispute_trail = CALC.calculate_with_trail(
        MatchedPair(
            contract=contract_item(item_code="CAM-002", agreed_amount="1000.00"),
            invoice=invoice_item(item_code="CAM-002", billed_amount="1250.00"),
        )
    )
    clean, clean_trail = CALC.calculate_with_trail(
        MatchedPair(
            contract=contract_item(item_code="CAM-001", agreed_amount="500.00"),
            invoice=invoice_item(item_code="CAM-001", billed_amount="500.00"),
        )
    )
    assert dispute.severity == Severity.DISPUTE
    assert clean.severity == Severity.WITHIN_TOLERANCE
    return AuditResult(
        audit_id="TEST01",
        use_case=UseCase.LEASE_CAM,
        findings=[dispute, clean],
        unmatched_invoice_items=[
            invoice_item(item_code="CAM-099", description="Capital Surcharge")
        ],
        total_agreed=Decimal("1500.00"),
        total_billed=Decimal("1750.00"),
        total_recoverable=Decimal("250.00"),
        audit_trail={"CAM-002": dispute_trail, "CAM-001": clean_trail},
    )


class TestRegionalDisputeGenerator:
    def test_letter_contains_exact_amounts_and_demand(self, tmp_path):
        path = RegionalDisputeGenerator(PROFILE, tmp_path).generate(_result())
        text = path.read_text(encoding="utf-8")
        assert "$250.00" in text  # exact overcharge
        assert "TOTAL CREDIT DEMANDED: $250.00" in text
        assert "Section 4.3" in text  # contract clause cited
        assert "30 days" in text

    def test_letter_excludes_within_tolerance_items(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result())
            .read_text(encoding="utf-8")
        )
        assert "CAM-002" in text
        assert "CAM-001" not in text

    def test_unmatched_charges_are_challenged(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result())
            .read_text(encoding="utf-8")
        )
        assert "CAM-099" in text
        assert "No Contractual Basis" in text

    def test_no_disputes_means_no_letter(self, tmp_path):
        result = _result()
        clean_only = AuditResult(
            audit_id="TEST02",
            use_case=UseCase.LEASE_CAM,
            findings=[
                f for f in result.findings if f.severity == Severity.WITHIN_TOLERANCE
            ],
        )
        assert RegionalDisputeGenerator(PROFILE, tmp_path).generate(clean_only) is None

    def test_evidence_appendix_shows_formula_and_confidence(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result())
            .read_text(encoding="utf-8")
        )
        assert "Evidence Appendix" in text
        assert (
            "variance_amount = billed_amount - agreed_amount = 1250.00 - 1000.00 = 250.00"
            in text
        )
        assert "100.00% confidence" in text

    def test_counsel_disclaimer_always_embedded(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result())
            .read_text(encoding="utf-8")
        )
        assert "reviewed by licensed counsel" in text


class TestJurisdictionalLanguage:
    def test_california_letter_cites_california_law(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result(), jurisdiction="CA")
            .read_text(encoding="utf-8")
        )
        assert "CALIFORNIA" in text
        assert "California Commercial Code" in text

    def test_texas_letter_cites_texas_law(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result(), jurisdiction="tx")  # case-insensitive
            .read_text(encoding="utf-8")
        )
        assert "Texas Business and Commerce Code" in text

    def test_unknown_jurisdiction_falls_back_to_default(self, tmp_path):
        text = (
            RegionalDisputeGenerator(PROFILE, tmp_path)
            .generate(_result(), jurisdiction="ZZ")
            .read_text(encoding="utf-8")
        )
        assert "NOTICE OF BILLING DISPUTE AND DEMAND FOR CURE" in text
        assert "General (no specific jurisdiction)" in text

    def test_registry_lists_available_jurisdictions(self):
        available = LegalClauseRegistry().available()
        assert "DEFAULT" in available
        assert {"CA", "NY", "TX", "IL"}.issubset(set(available))


class TestSummaryReporter:
    def test_excel_workbook_is_written(self, tmp_path):
        path = SummaryReporter(tmp_path).write_excel(_result())
        assert path.is_file()
        assert path.suffix == ".xlsx"
