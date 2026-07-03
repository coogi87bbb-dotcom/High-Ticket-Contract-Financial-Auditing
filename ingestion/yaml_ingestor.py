"""YAML parser for policy/fee-schedule documents (e.g. insurance fee schedules).

Expects a top-level `line_items:` list where each item's keys are the same column
headers the use-case profile maps (e.g. "CPT Code", "Allowed Amount"). Monetary
values MUST be quoted strings in the YAML — an unquoted number parses as a float,
which would violate the exact-Decimal guarantee, so the whole document is rejected
with a clear error telling the author to quote it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ingestion.base import BaseIngestor


class YamlIngestor(BaseIngestor):
    module_name = "ingestion.yaml"

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("line_items") if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            raise ValueError(
                "YAML policy document must contain a non-empty 'line_items:' list"
            )
        rows: list[dict[str, str]] = []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"line_items entry {idx} is not a key/value mapping")
            row: dict[str, str] = {}
            for key, value in item.items():
                if isinstance(value, bool) or isinstance(value, float):
                    raise ValueError(
                        f"line_items entry {idx}, field '{key}': unquoted number "
                        f"{value!r} parses as a float. Quote all monetary/decimal "
                        f'values (e.g. "150.00") so they stay exact.'
                    )
                row[str(key)] = "" if value is None else str(value)
            rows.append(row)
        return rows
