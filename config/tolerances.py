"""Use-case profile loading: tolerance rules + column mappings from config/use_cases/*.yaml.

Adding a new audit domain = adding one YAML file here. No engine code changes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config.models import ErrorModel, ToleranceProfile, UseCase

logger = logging.getLogger(__name__)

USE_CASE_DIR = Path(__file__).parent / "use_cases"


class UseCaseProfile(BaseModel):
    """Everything the engine needs to know about one audit domain."""

    model_config = ConfigDict(strict=False, frozen=True)

    use_case: UseCase
    display_name: str
    domain_label: str
    recipient_role: str
    response_deadline_days: int = 30
    dispute_template: str = "dispute_letter.md.j2"
    match_keys: list[str] = Field(default_factory=lambda: ["item_code"])
    # Minimum token-sort similarity (0–100) for the fuzzy description fallback.
    fuzzy_threshold: Decimal = Decimal("85.00")
    tolerance: ToleranceProfile
    contract_columns: dict[str, str]
    invoice_columns: dict[str, str]

    @field_validator("fuzzy_threshold", mode="before")
    @classmethod
    def _v_fuzzy_threshold(cls, v: object) -> Decimal:
        if isinstance(v, bool) or isinstance(v, float):
            raise ValueError(
                "fuzzy_threshold must be a quoted string in YAML, not a float"
            )
        return Decimal(str(v))


def available_use_cases() -> list[str]:
    """Names of every YAML profile on disk (without extension)."""
    if not USE_CASE_DIR.is_dir():
        return []
    return sorted(p.stem for p in USE_CASE_DIR.glob("*.yaml"))


def load_use_case_profile(use_case: str) -> UseCaseProfile | ErrorModel:
    """Load and validate one use-case YAML. Returns ErrorModel on any failure."""
    path = USE_CASE_DIR / f"{use_case}.yaml"
    if not path.is_file():
        return ErrorModel(
            module="config.tolerances",
            operation="load_use_case_profile",
            message=f"Unknown use case '{use_case}'",
            detail=f"No profile at {path}. Available: {', '.join(available_use_cases())}",
        ).log()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return UseCaseProfile.model_validate(raw)
    except (yaml.YAMLError, ValidationError, OSError) as exc:
        return ErrorModel(
            module="config.tolerances",
            operation="load_use_case_profile",
            message=f"Invalid use-case profile '{use_case}'",
            detail=str(exc),
        ).log()
