"""Universal Financial Audit & Variance Guard Engine — CLI entry point.

Usage:
    python main.py audit --use-case lease_cam --contract data/input/contract.csv --invoice data/input/invoice.csv
    python main.py list-use-cases
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from analyzer.engine import AuditEngine
from config.models import AuditContext, ErrorModel
from config.tolerances import available_use_cases, load_use_case_profile
from logging_setup import setup_logging
from reporter.generator import RegionalDisputeGenerator
from reporter.summary_reporter import SummaryReporter

OUTPUT_DIR = Path(__file__).parent / "data" / "output"

app = typer.Typer(
    help="Universal Financial Audit & Variance Guard Engine", no_args_is_help=True
)
console = Console()
logger = logging.getLogger(__name__)


@app.command("list-use-cases")
def list_use_cases() -> None:
    """Show every audit domain configured in config/use_cases/."""
    for name in available_use_cases():
        console.print(f"  • {name}")


@app.command()
def audit(
    use_case: str = typer.Option(
        ..., help="Audit domain, e.g. lease_cam (see list-use-cases)"
    ),
    contract: Path = typer.Option(..., help="Path to the contract document"),
    invoice: Path = typer.Option(..., help="Path to the invoice/bill document"),
    jurisdiction: str = typer.Option(
        "DEFAULT",
        help="State code for legal language in the dispute letter (e.g. CA, NY, TX)",
    ),
) -> None:
    """Run a full contract-vs-invoice variance audit."""
    setup_logging()
    try:
        profile = load_use_case_profile(use_case)
        if isinstance(profile, ErrorModel):
            console.print(f"[red]✖ {profile.message}[/red]\n  {profile.detail}")
            raise typer.Exit(code=1)

        audit_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        context = AuditContext(
            audit_id=audit_id,
            use_case=profile.use_case,
            contract_path=str(contract),
            invoice_path=str(invoice),
            tolerance_profile=profile.tolerance,
            jurisdiction=jurisdiction.strip().upper(),
        )
        console.print(f"[bold]Running audit {audit_id}[/bold] — {profile.display_name}")

        result = AuditEngine(profile).run(context)

        fatal = (
            not result.findings and not result.unmatched_invoice_items and result.errors
        )
        if fatal:
            console.print("[red]✖ Audit could not be completed:[/red]")
            for err in result.errors:
                console.print(f"  [red]•[/red] {err.message}\n    {err.detail}")
            raise typer.Exit(code=1)

        reporter = SummaryReporter(OUTPUT_DIR)
        reporter.print_console_summary(result)

        excel_path = reporter.write_excel(result)
        if isinstance(excel_path, ErrorModel):
            console.print(f"[red]✖ {excel_path.message}[/red]")
        else:
            console.print(f"📊 Excel summary: [cyan]{excel_path}[/cyan]")

        letter = RegionalDisputeGenerator(profile, OUTPUT_DIR).generate(
            result, jurisdiction=context.jurisdiction
        )
        if isinstance(letter, ErrorModel):
            console.print(f"[red]✖ {letter.message}[/red]")
        elif letter is None:
            console.print("✅ No dispute-grade findings — no letter generated.")
        else:
            console.print(f"⚖️  Dispute letter: [cyan]{letter}[/cyan]")
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 — the CLI never shows a traceback
        logger.exception("Unhandled CLI failure")
        console.print(f"[red]✖ Unexpected error ({type(exc).__name__}): {exc}[/red]")
        console.print("  Details were logged to data/output/audit.log")
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
