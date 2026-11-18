#!/usr/bin/env python3
"""Cooler Panel Auto-Tester entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root on path when run as script
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from app.ui.main_window import run_app

    run_app()


if __name__ == "__main__":
    main()
