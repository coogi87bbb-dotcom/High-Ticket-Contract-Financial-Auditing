# Universal Financial Audit & Variance Guard Engine

## 1. Project Overview & State

**Mission:** A configuration-driven audit engine that compares what a contract *says you should be charged* against what an invoice *actually charged you*, flags every discrepancy with exact math, and auto-generates dispute documentation.

**Supported use cases (all driven by config, not code changes):**
1. Commercial Lease vs. CAM Reconciliation statements
2. Freight Contracts vs. Carrier Bills
3. SaaS Contract Terms vs. Vendor Invoices
4. Corporate Expense Policies vs. Employee Receipts
5. Medical Claims vs. Provider Bills

**Build status:** `V2 COMPLETE — audit trail + fuzzy matching + regional legal reporter live, 49/49 tests passing, ruff clean`

**Active tasks:**
- [x] Phase 1: CLAUDE.md project memory initialized
- [x] Phase 2: requirements.txt + environment setup commands delivered
- [x] Phase 3: Architectural blueprint presented to founder
- [x] Phase 4: /config Pydantic models + 5 use-case YAML profiles
- [x] Phase 5: /ingestion document factory (CSV, Excel, PDF)
- [x] Phase 6: /analyzer variance engine (matcher, calculator, orchestrator)
- [x] Phase 7: /reporter dispute letters + Excel/console summaries
- [x] Phase 8: End-to-end verified on sample Lease vs. CAM dataset ($900.00 recoverable found)
- [x] V2 Upgrade 1 — Audit Trail Guarantee: immutable `AuditResult.audit_trail` (config/schemas.py) with exact formulas, raw pre-Decimal source values, contract clause, match confidence; rendered as the letter's Evidence Appendix + a 4th Excel sheet
- [x] V2 Upgrade 2 — Semantic fuzzy matching: token-sort similarity fallback (difflib, zero deps, deterministic) with per-use-case `fuzzy_threshold` in YAML (default 85)
- [x] V2 Upgrade 3 — Regional Legal Reporter: `reporter/generator.py` (replaces dispute_generator.py) pulls jurisdiction language from `config/legal/jurisdictions.yaml` (DEFAULT/CA/NY/TX/IL); CLI takes `--jurisdiction CA`; unknown codes fall back to DEFAULT; every letter embeds a counsel-review disclaimer

**Next milestones (not started):**
- Real-document hardening: test PDF ingestion against an actual CAM statement/freight bill
- Tune the four starter YAML profiles (freight, saas, expense, medical) to real source formats
- Optional: batch mode (audit a folder of invoices), PDF letter output, web UI

**Known constraints:**
- `click` is pinned to 8.1.7 — click 8.2+ breaks typer 0.12.x option parsing. Do not upgrade one without the other.
- Sample demo data lives in `data/input/sample_lease_contract.csv` + `sample_cam_statement.csv` (planted findings: CAM-002 overcharge, CAM-003 cap breach, CAM-004 review-band, CAM-099 unmatched charge, CAM-010 never billed).

## 2. Core Architecture

```
High-Ticket-Contract-Financial-Auditing/
├── CLAUDE.md                  # This file — persistent project memory
├── requirements.txt           # Pinned dependencies
├── .env                       # Secrets/settings (never committed)
├── main.py                    # CLI entry point — orchestrates the pipeline
│
├── config/                    # SCHEMAS & RULES (the "law" of the system)
│   ├── __init__.py
│   ├── models.py              # AuditContext (incl. jurisdiction), line items (incl.
│   │                          #   raw_source), VarianceFinding, AuditResult
│   │                          #   (incl. immutable audit_trail), ErrorModel
│   ├── schemas.py             # AuditTrailEntry (frozen evidence record), MatchMethod
│   ├── tolerances.py          # ToleranceProfile + UseCaseProfile (incl. fuzzy_threshold)
│   ├── legal/
│   │   └── jurisdictions.yaml # Per-state legal language (DEFAULT/CA/NY/TX/IL)
│   └── use_cases/             # One YAML per audit domain (lease_cam.yaml,
│                              #   freight.yaml, saas.yaml, expense.yaml, medical.yaml)
│
├── ingestion/                 # DOCUMENT INTAKE (the "mailroom")
│   ├── __init__.py
│   ├── base.py                # BaseIngestor abstract class + IngestResult model
│   ├── factory.py             # IngestorFactory — picks parser by file type
│   ├── excel_ingestor.py      # .xlsx/.xls via openpyxl/pandas
│   ├── csv_ingestor.py        # .csv via pandas
│   └── pdf_ingestor.py        # .pdf via pdfplumber (tables + text)
│
├── analyzer/                  # THE MATH ENGINE (the "auditor")
│   ├── __init__.py
│   ├── matcher.py             # LineItemMatcher — pairs contract ↔ invoice items
│   ├── variance.py            # VarianceCalculator — Decimal-exact deltas vs. tolerances
│   └── engine.py              # AuditEngine — orchestrates match → calculate → classify
│
├── reporter/                  # OUTPUT (the "lawyer's desk")
│   ├── __init__.py
│   ├── templates/             # Jinja2 dispute letter (jurisdiction-aware + evidence appendix)
│   ├── generator.py           # RegionalDisputeGenerator + LegalClauseRegistry
│   └── summary_reporter.py    # Console table + Excel (Findings/Unmatched/Audit Trail/Summary)
│
├── data/
│   ├── input/                 # Drop contracts + invoices here
│   └── output/                # Generated reports + dispute letters land here
│
└── tests/
    ├── test_models.py
    ├── test_ingestion.py
    ├── test_analyzer.py
    └── test_reporter.py
```

**Pipeline flow:** `main.py` → ingestion (parse docs into validated models) → analyzer (match + compute variances) → reporter (dispute letters + summaries). Every module communicates only through Pydantic models defined in `config/models.py`.

## 3. Dev Commands

```bash
# Activate environment (run once per terminal session)
source .venv/bin/activate

# Run an audit (example: lease CAM use case, California legal language)
python main.py audit --use-case lease_cam --contract data/input/sample_lease_contract.csv --invoice data/input/sample_cam_statement.csv --jurisdiction CA

# Lint & format
ruff check . --fix
ruff format .

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_analyzer.py -v
```

## 4. Code Style & Guarantees (NON-NEGOTIABLE)

1. **Pydantic v2 strict validation.** Every data structure crossing a module boundary is a Pydantic `BaseModel` with `model_config = ConfigDict(strict=True, frozen=True)` where practical. No raw dicts between modules.
2. **Zero-exception policy.** No function lets an exception escape to the caller. Pattern: catch → log with context → return a typed `ErrorModel` (or a `Result`-style union). `main.py` always exits cleanly with a human-readable message.
3. **Decimal-only money math.** All financial values use `decimal.Decimal` with explicit quantization (`.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`). Floats are FORBIDDEN for money — ingestion converts immediately at the boundary, parsing from *strings*, never from floats.
4. **Configuration over code.** New audit use cases are added by writing a YAML profile in `config/use_cases/` — never by editing engine logic.
5. **Static context for AI efficiency.** Schemas, rules, and architecture live in files (this one first). Keep prompts short; reference files instead of restating them.
6. **Type hints everywhere.** Full annotations; `ruff` clean before any commit.
7. **Every finding is evidence-backed.** The engine writes a frozen `AuditTrailEntry` per finding (exact formula, raw pre-Decimal source values, tolerance rule fired, match method + confidence, contract clause). Reporters surface it (letter appendix, Excel sheet); nothing may mutate it after the engine writes it.
8. **Legal language is config, not code.** Jurisdiction blocks live in `config/legal/jurisdictions.yaml`; letters always embed the counsel-review disclaimer. Never fabricate specific statute section numbers.
