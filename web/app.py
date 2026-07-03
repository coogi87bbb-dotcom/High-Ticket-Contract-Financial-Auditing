"""FastAPI web layer: upload contract + invoice in a browser, get the audit back.

A thin wrapper around the existing engine — no audit logic lives here. Protected
by HTTP Basic auth against the APP_PASSWORD environment variable; if that variable
is unset the app refuses every request rather than running open, because uploaded
documents are client financial records.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jinja2 import Environment, FileSystemLoader

from analyzer.engine import AuditEngine
from config.models import AuditContext, ErrorModel
from config.tolerances import available_use_cases, load_use_case_profile
from ingestion.factory import IngestorFactory
from logging_setup import setup_logging
from reporter.generator import LegalClauseRegistry, RegionalDisputeGenerator
from reporter.summary_reporter import SummaryReporter

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "data" / "output"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB per document
AUDIT_ID_RE = re.compile(r"^\d{8}_\d{6}$")

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Universal Financial Audit & Variance Guard Engine")
security = HTTPBasic()
_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def require_password(
    credentials: HTTPBasicCredentials = Depends(security),
) -> None:
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server is not configured: set the APP_PASSWORD environment "
                "variable before serving audits."
            ),
        )
    if not secrets.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
            headers={"WWW-Authenticate": "Basic"},
        )


def _render(template: str, status_code: int = 200, **context: object) -> HTMLResponse:
    html = _templates.get_template(template).render(**context)
    return HTMLResponse(html, status_code=status_code)


def _error_page(message: str, detail: str = "", status_code: int = 400) -> HTMLResponse:
    return _render(
        "error.html.j2", status_code=status_code, message=message, detail=detail
    )


@app.get("/", response_class=HTMLResponse)
def index(_: None = Depends(require_password)) -> HTMLResponse:
    return _render(
        "index.html.j2",
        use_cases=available_use_cases(),
        jurisdictions=LegalClauseRegistry().available(),
        supported_types=", ".join(IngestorFactory.supported_types()),
    )


@app.post("/audit", response_class=HTMLResponse)
async def run_audit(
    _: None = Depends(require_password),
    use_case: str = Form(...),
    jurisdiction: str = Form("DEFAULT"),
    contract: UploadFile = File(...),
    invoice: UploadFile = File(...),
) -> HTMLResponse:
    workdir: Path | None = None
    try:
        profile = load_use_case_profile(use_case)
        if isinstance(profile, ErrorModel):
            return _error_page(profile.message, profile.detail)

        workdir = Path(tempfile.mkdtemp(prefix="vge_upload_"))
        saved: dict[str, Path] = {}
        for label, upload in (("contract", contract), ("invoice", invoice)):
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in IngestorFactory.supported_types():
                return _error_page(
                    f"Unsupported {label} file type '{suffix or '(none)'}'",
                    f"Supported types: {', '.join(IngestorFactory.supported_types())}",
                )
            data = await upload.read()
            if not data:
                return _error_page(f"The {label} file is empty.")
            if len(data) > MAX_UPLOAD_BYTES:
                return _error_page(
                    f"The {label} file exceeds the 10 MB upload limit.",
                    f"Received {len(data):,} bytes.",
                )
            path = workdir / f"{label}{suffix}"
            path.write_bytes(data)
            saved[label] = path

        audit_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        context = AuditContext(
            audit_id=audit_id,
            use_case=profile.use_case,
            contract_path=str(saved["contract"]),
            invoice_path=str(saved["invoice"]),
            tolerance_profile=profile.tolerance,
            jurisdiction=jurisdiction.strip().upper() or "DEFAULT",
        )
        result = AuditEngine(profile).run(context)

        reporter = SummaryReporter(OUTPUT_DIR)
        excel = reporter.write_excel(result)
        letter = RegionalDisputeGenerator(profile, OUTPUT_DIR).generate(
            result, jurisdiction=context.jurisdiction
        )
        return _render(
            "results.html.j2",
            result=result,
            profile=profile,
            audit_id=audit_id,
            jurisdiction=context.jurisdiction,
            excel_ready=not isinstance(excel, ErrorModel),
            letter_ready=isinstance(letter, Path),
            letter_skipped=letter is None,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the web layer never shows a traceback
        logger.exception("Unhandled web audit failure")
        return _error_page(
            f"Unexpected error ({type(exc).__name__}).",
            "Details were logged to data/output/audit.log.",
            status_code=500,
        )
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


@app.get("/download/{audit_id}/{kind}")
def download(
    audit_id: str, kind: str, _: None = Depends(require_password)
) -> FileResponse:
    if not AUDIT_ID_RE.match(audit_id) or kind not in {"excel", "letter"}:
        raise HTTPException(status_code=404, detail="Unknown download.")
    name = f"audit_{audit_id}.xlsx" if kind == "excel" else f"dispute_{audit_id}.md"
    path = OUTPUT_DIR / name
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "File not found. Free-tier storage is temporary — re-run the audit "
                "and download the results right away."
            ),
        )
    return FileResponse(path, filename=name)
