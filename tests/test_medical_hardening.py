"""Real-document hardening: messy hospital bill vs. YAML insurance fee schedule.

The bill plants three classic medical overcharge schemes (upcoding, straight
overcharge, duplicate/unbundled supplies breaking a global cap) plus real-world
formatting chaos ($ signs, four date formats, padded codes, a junk TOTAL row).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from analyzer.engine import AuditEngine
from config.models import AuditContext, Severity
from config.schemas import MatchMethod
from config.tolerances import load_use_case_profile
from ingestion.factory import IngestorFactory
from ingestion.yaml_ingestor import YamlIngestor

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def result():
    profile = load_use_case_profile("medical")
    context = AuditContext(
        audit_id="MEDTEST",
        use_case=profile.use_case,
        contract_path=str(ROOT / "data/input/medical_policy.yaml"),
        invoice_path=str(ROOT / "data/input/hospital_bill_messy.csv"),
        tolerance_profile=profile.tolerance,
        jurisdiction="IL",
    )
    return AuditEngine(profile).run(context)


class TestMedicalHardening:
    def test_total_recovered_is_exact(self, result):
        # 200 (upcoding) + 80 (CBC overcharge) + 10 + 60 + 45 (supplies) = 395.00
        assert result.total_recoverable == Decimal("395.00")

    def test_upcoding_caught_by_fuzzy_match(self, result):
        entry = result.audit_trail["99214"]
        assert entry.match_method == MatchMethod.FUZZY_DESCRIPTION
        assert entry.match_confidence >= Decimal("85")
        finding = next(f for f in result.findings if f.trail_key == "99214")
        assert finding.variance_amount == Decimal("200.00")
        assert finding.severity == Severity.DISPUTE

    def test_straight_overcharge_caught(self, result):
        finding = next(f for f in result.findings if f.item_code == "85025")
        assert finding.variance_amount == Decimal("80.00")
        assert finding.severity == Severity.DISPUTE

    def test_duplicate_supplies_flagged_against_global_cap(self, result):
        dup_entries = [
            e
            for e in result.audit_trail.values()
            if e.match_method == MatchMethod.DUPLICATE_KEY
        ]
        assert len(dup_entries) == 2  # the 2nd and 3rd 0250 lines
        for entry in dup_entries:
            assert "aggregate 165.00 > global cap 100.00" in entry.severity_rule
        dup_findings = [
            f for f in result.findings if f.item_code == "0250" and f.cap_breached
        ]
        assert sum(f.variance_amount for f in dup_findings) >= Decimal("105.00")

    def test_clean_line_stays_clean(self, result):
        finding = next(f for f in result.findings if f.item_code == "36415")
        assert finding.severity == Severity.WITHIN_TOLERANCE

    def test_junk_total_row_rejected_not_crashed(self, result):
        assert any("rejected" in e.message for e in result.errors)

    def test_messy_currency_strings_became_exact_decimals(self, result):
        entry = result.audit_trail["99214"]
        assert entry.raw_invoice_row["billed_amount"] == "$350.00"  # pre-Decimal
        finding = next(f for f in result.findings if f.trail_key == "99214")
        assert finding.billed_amount == Decimal("350.00")  # post-Decimal, exact


class TestYamlIngestion:
    def test_factory_routes_yaml(self):
        ingestor = IngestorFactory.for_file(ROOT / "data/input/medical_policy.yaml")
        assert isinstance(ingestor, YamlIngestor)

    def test_unquoted_float_in_yaml_is_rejected(self, tmp_path):
        bad = tmp_path / "bad_policy.yaml"
        bad.write_text(
            "line_items:\n  - CPT Code: '99214'\n    Allowed Amount: 150.00\n"
        )
        profile = load_use_case_profile("medical")
        from config.models import ContractLineItem

        result = YamlIngestor().ingest(bad, profile.contract_columns, ContractLineItem)
        assert result.fatal
        assert "float" in result.errors[0].detail.lower()
