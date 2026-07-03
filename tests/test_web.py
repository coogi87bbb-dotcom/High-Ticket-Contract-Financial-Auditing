"""Web layer guarantees: password gate, real audits through the browser flow,
friendly failures, and working downloads."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.app import app

ROOT = Path(__file__).parent.parent
PASSWORD = "test-secret"
AUTH = ("auditor", PASSWORD)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    return TestClient(app)


def _lease_files():
    return {
        "contract": (
            "lease.csv",
            (ROOT / "data/input/sample_lease_contract.csv").read_bytes(),
            "text/csv",
        ),
        "invoice": (
            "cam.csv",
            (ROOT / "data/input/sample_cam_statement.csv").read_bytes(),
            "text/csv",
        ),
    }


class TestAuth:
    def test_no_credentials_is_challenged(self, client):
        assert client.get("/").status_code == 401

    def test_wrong_password_rejected(self, client):
        assert client.get("/", auth=("auditor", "wrong")).status_code == 401

    def test_correct_password_shows_form(self, client):
        response = client.get("/", auth=AUTH)
        assert response.status_code == 200
        assert "lease_cam" in response.text
        assert "DEFAULT" in response.text

    def test_missing_app_password_refuses_service(self, monkeypatch):
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        response = TestClient(app).get("/", auth=AUTH)
        assert response.status_code == 503
        assert "APP_PASSWORD" in response.text


class TestAuditFlow:
    def test_lease_audit_finds_900_and_downloads_work(self, client):
        response = client.post(
            "/audit",
            auth=AUTH,
            data={"use_case": "lease_cam", "jurisdiction": "CA"},
            files=_lease_files(),
        )
        assert response.status_code == 200
        assert "$900.00" in response.text
        assert "CAM-099" in response.text  # unmatched charge surfaced

        audit_id = re.search(r"/download/(\d{8}_\d{6})/excel", response.text).group(1)
        excel = client.get(f"/download/{audit_id}/excel", auth=AUTH)
        assert excel.status_code == 200
        assert excel.content[:2] == b"PK"  # xlsx is a zip container
        letter = client.get(f"/download/{audit_id}/letter", auth=AUTH)
        assert letter.status_code == 200
        assert "TOTAL CREDIT DEMANDED: $900.00" in letter.text
        assert "CALIFORNIA" in letter.text

    def test_medical_audit_finds_395(self, client):
        response = client.post(
            "/audit",
            auth=AUTH,
            data={"use_case": "medical", "jurisdiction": "IL"},
            files={
                "contract": (
                    "policy.yaml",
                    (ROOT / "data/input/medical_policy.yaml").read_bytes(),
                    "application/yaml",
                ),
                "invoice": (
                    "bill.csv",
                    (ROOT / "data/input/hospital_bill_messy.csv").read_bytes(),
                    "text/csv",
                ),
            },
        )
        assert response.status_code == 200
        assert "$395.00" in response.text


class TestFriendlyFailures:
    def test_unsupported_file_type(self, client):
        files = _lease_files()
        files["contract"] = (
            "contract.docx",
            b"not really a docx",
            "application/msword",
        )
        response = client.post(
            "/audit", auth=AUTH, data={"use_case": "lease_cam"}, files=files
        )
        assert response.status_code == 400
        assert "Unsupported contract file type" in response.text

    def test_empty_file(self, client):
        files = _lease_files()
        files["invoice"] = ("empty.csv", b"", "text/csv")
        response = client.post(
            "/audit", auth=AUTH, data={"use_case": "lease_cam"}, files=files
        )
        assert response.status_code == 400
        assert "empty" in response.text

    def test_unknown_use_case(self, client):
        response = client.post(
            "/audit", auth=AUTH, data={"use_case": "utilities"}, files=_lease_files()
        )
        assert response.status_code == 400
        assert "Unknown use case" in response.text

    def test_download_path_traversal_blocked(self, client):
        assert client.get("/download/../etc/passwd", auth=AUTH).status_code in (
            404,
            422,
        )
        assert (
            client.get("/download/20990101_000000/excel", auth=AUTH).status_code == 404
        )
