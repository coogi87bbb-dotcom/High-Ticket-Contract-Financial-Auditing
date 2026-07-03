"""SummaryReporter: console findings table (rich) and Excel workbook (xlsxwriter).

All variance math is already finished in exact Decimal by the analyzer; values are
converted to display formats here only for presentation, never recalculated.
"""

from __future__ import annotations

import logging
from pathlib import Path

import xlsxwriter

from config.models import AuditResult, ErrorModel, Severity
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

_SEVERITY_STYLE = {
    Severity.WITHIN_TOLERANCE: "green",
    Severity.REVIEW: "yellow",
    Severity.DISPUTE: "bold red",
}


class SummaryReporter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.console = Console()

    def print_console_summary(self, result: AuditResult) -> None:
        table = Table(title=f"Audit {result.audit_id} — Findings")
        table.add_column("Item")
        table.add_column("Description")
        table.add_column("Agreed", justify="right")
        table.add_column("Billed", justify="right")
        table.add_column("Variance", justify="right")
        table.add_column("Severity")

        for f in result.findings:
            style = _SEVERITY_STYLE[f.severity]
            table.add_row(
                f.item_code,
                f.description,
                f"${f.agreed_amount}",
                f"${f.billed_amount}",
                f"${f.variance_amount}",
                f"[{style}]{f.severity}[/{style}]",
            )
        self.console.print(table)

        for item in result.unmatched_invoice_items:
            self.console.print(
                f"[bold red]⚠ Unmatched invoice charge:[/bold red] "
                f"{item.item_code} — {item.description} (${item.billed_amount})"
            )
        for item in result.unmatched_contract_items:
            self.console.print(
                f"[yellow]• Contract item never billed:[/yellow] "
                f"{item.item_code} — {item.description}"
            )
        if result.errors:
            self.console.print(
                f"[red]{len(result.errors)} error(s) — see data/output/audit.log[/red]"
            )

        self.console.print(
            f"\n[bold]Total agreed:[/bold] ${result.total_agreed}   "
            f"[bold]Total billed:[/bold] ${result.total_billed}   "
            f"[bold red]Recoverable (dispute-grade): ${result.total_recoverable}[/bold red]\n"
        )

    def write_excel(self, result: AuditResult) -> Path | ErrorModel:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.output_dir / f"audit_{result.audit_id}.xlsx"
            workbook = xlsxwriter.Workbook(str(out_path))
            money_fmt = workbook.add_format({"num_format": "$#,##0.00"})
            header_fmt = workbook.add_format({"bold": True, "bg_color": "#DDEBF7"})

            findings_sheet = workbook.add_worksheet("Findings")
            headers = [
                "Item Code",
                "Description",
                "Clause",
                "Invoice Ref",
                "Agreed",
                "Billed",
                "Variance",
                "Variance %",
                "Cap Breached",
                "Severity",
            ]
            for col, h in enumerate(headers):
                findings_sheet.write(0, col, h, header_fmt)
            for row, f in enumerate(result.findings, start=1):
                findings_sheet.write(row, 0, f.item_code)
                findings_sheet.write(row, 1, f.description)
                findings_sheet.write(row, 2, f.contract_clause or "")
                findings_sheet.write(row, 3, f.invoice_ref)
                # float() below is display-only; the Decimal math is already final.
                findings_sheet.write_number(row, 4, float(f.agreed_amount), money_fmt)
                findings_sheet.write_number(row, 5, float(f.billed_amount), money_fmt)
                findings_sheet.write_number(row, 6, float(f.variance_amount), money_fmt)
                findings_sheet.write(
                    row, 7, str(f.variance_pct) if f.variance_pct is not None else ""
                )
                findings_sheet.write(row, 8, "YES" if f.cap_breached else "")
                findings_sheet.write(row, 9, f.severity.value)
            findings_sheet.set_column(0, 3, 22)
            findings_sheet.set_column(4, 9, 14)

            unmatched_sheet = workbook.add_worksheet("Unmatched")
            for col, h in enumerate(["Side", "Item Code", "Description", "Amount"]):
                unmatched_sheet.write(0, col, h, header_fmt)
            row = 1
            for item in result.unmatched_invoice_items:
                unmatched_sheet.write(row, 0, "INVOICE (no contract basis)")
                unmatched_sheet.write(row, 1, item.item_code)
                unmatched_sheet.write(row, 2, item.description)
                unmatched_sheet.write_number(
                    row, 3, float(item.billed_amount), money_fmt
                )
                row += 1
            for item in result.unmatched_contract_items:
                unmatched_sheet.write(row, 0, "CONTRACT (never billed)")
                unmatched_sheet.write(row, 1, item.item_code)
                unmatched_sheet.write(row, 2, item.description)
                unmatched_sheet.write_number(
                    row, 3, float(item.agreed_amount), money_fmt
                )
                row += 1
            unmatched_sheet.set_column(0, 2, 28)

            trail_sheet = workbook.add_worksheet("Audit Trail")
            trail_headers = [
                "Key",
                "Match Method",
                "Confidence %",
                "Matched On",
                "Variance Formula",
                "Percentage Formula",
                "Severity Rule",
                "Contract Clause",
                "Raw Contract Row",
                "Raw Invoice Row",
            ]
            for col, h in enumerate(trail_headers):
                trail_sheet.write(0, col, h, header_fmt)
            for row, (key, entry) in enumerate(result.audit_trail.items(), start=1):
                trail_sheet.write(row, 0, key)
                trail_sheet.write(row, 1, entry.match_method.value)
                trail_sheet.write(row, 2, str(entry.match_confidence))
                trail_sheet.write(row, 3, entry.matched_on)
                trail_sheet.write(row, 4, entry.variance_formula)
                trail_sheet.write(row, 5, entry.pct_formula or "")
                trail_sheet.write(row, 6, entry.severity_rule)
                trail_sheet.write(row, 7, entry.contract_clause or "")
                trail_sheet.write(row, 8, str(entry.raw_contract_row))
                trail_sheet.write(row, 9, str(entry.raw_invoice_row))
            trail_sheet.set_column(0, 3, 24)
            trail_sheet.set_column(4, 9, 48)

            summary_sheet = workbook.add_worksheet("Summary")
            rows = [
                ("Audit ID", result.audit_id),
                ("Use Case", result.use_case.value),
                ("Total Agreed", float(result.total_agreed)),
                ("Total Billed", float(result.total_billed)),
                ("Total Recoverable (Dispute)", float(result.total_recoverable)),
                ("Findings", len(result.findings)),
                ("Dispute-Grade Findings", len(result.dispute_findings)),
                ("Unmatched Invoice Charges", len(result.unmatched_invoice_items)),
                ("Errors", len(result.errors)),
            ]
            for r, (label, value) in enumerate(rows):
                summary_sheet.write(r, 0, label, header_fmt)
                if isinstance(value, float):
                    summary_sheet.write_number(r, 1, value, money_fmt)
                else:
                    summary_sheet.write(r, 1, value)
            summary_sheet.set_column(0, 0, 30)
            summary_sheet.set_column(1, 1, 20)

            workbook.close()
            logger.info("Excel summary written to %s", out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001 — zero-exception boundary
            return ErrorModel(
                module="reporter.summary_reporter",
                operation="write_excel",
                message="Failed to write Excel summary",
                detail=f"{type(exc).__name__}: {exc}",
            ).log()
