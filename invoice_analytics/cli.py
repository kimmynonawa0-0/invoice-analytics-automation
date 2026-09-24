"""Command-line entry point for invoice batch processing."""

import argparse
import logging
from pathlib import Path

from . import __version__
from .pipeline import process_directory
from .reporting import write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract supported PDF invoices and generate spending reports.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--input", type=Path, default=Path("data/samples"), help="Folder containing PDFs (default: data/samples).")
    parser.add_argument("--output", type=Path, default=Path("outputs"), help="Report folder (default: outputs).")
    parser.add_argument("--fail-on-review", action="store_true", help="Exit with status 2 after writing reports if any file requires review.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        result = process_directory(args.input)
        summary = write_reports(result, args.output)
    except (ValueError, OSError) as exc:
        logging.error("%s", exc)
        return 1

    logging.info("Processed %d PDFs: %d accepted, %d require review.", result.processed, len(result.invoices), len(result.issues))
    for currency, data in summary["currencies"].items():
        logging.info("%s total: %s", currency, data["total"])
    logging.info("Dashboard: %s", (args.output / "dashboard.html").resolve())
    return 2 if args.fail_on_review and result.issues else 0
