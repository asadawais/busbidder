"""Command-line interface.

Stage 1 only provides the ``validate`` command. Searching, ranking and booking
are added in later stages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .exceptions import BusBidderError
from .loader import load_marketplace
from .logging_setup import configure_logging
from .validation import Action


def cmd_validate(args: argparse.Namespace) -> int:
    result = load_marketplace(args.data, quarantine_dir=config.QUARANTINE_DIR)
    report = result.report
    market = result.marketplace
    print(f"Companies: {len(market.companies)}   Destinations: {len(market.destinations)}")
    for line in report.summary_lines():
        print(line)
    if args.verbose:
        for action in (Action.REJECTED, Action.REVIEW, Action.CORRECTED):
            issues = [i for i in report.issues if i.action is action]
            if issues:
                print(f"\n{action.value.upper()} ({len(issues)})")
                for issue in issues:
                    print(f"  row {issue.row:>5}  {issue.record_id:<28} "
                          f"{issue.field}: {issue.message}")
    print(f"\nQuarantine files: {config.QUARANTINE_DIR}")
    print(f"Log file:         {config.LOG_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m busbidder",
        description="BusBidder Spain - filter, rank and book bus offers from Madrid.",
    )
    parser.add_argument("--data", type=Path, default=config.DATA_DIR,
                        help="folder containing companies.json, destinations.csv, offers.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="load and validate the data files")
    validate.add_argument("-v", "--verbose", action="store_true",
                          help="list every rejected, corrected and review record")
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(config.LOG_FILE)
    try:
        return args.func(args)
    except BusBidderError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
