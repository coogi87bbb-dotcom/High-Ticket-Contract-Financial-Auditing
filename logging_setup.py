"""Central logging: full detail to data/output/audit.log, warnings+ to the console."""

from __future__ import annotations

import logging
from pathlib import Path

LOG_DIR = Path(__file__).parent / "data" / "output"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. repeat CLI calls in tests)
        return
    root.setLevel(logging.INFO)

    file_handler = logging.FileHandler(LOG_DIR / "audit.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console_handler)
