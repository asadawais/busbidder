"""Command-line interface.

Commands: ``validate`` (check the data files) and ``search`` (filter and rank
offers for one trip). Booking is added in a later stage.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .display import format_ranking, format_request
from .exceptions import BusBidderError, InvalidRequestError
from .loader import load_marketplace
from .logging_setup import configure_logging
from .ranking import search
from .request import TimeWindow, TravelRequest
from .validation import Action
from .weights import Weights


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


def _parse_date(text: str):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidRequestError(f"Date {text!r} must use YYYY-MM-DD") from None


def build_request(args: argparse.Namespace, market) -> TravelRequest:
    destination = market.find_destination(args.to)
    if destination is None:
        names = ", ".join(sorted(d.name for d in market.destinations.values()))
        raise InvalidRequestError(f"Unknown destination {args.to!r}. Choose from: {names}")
    earliest = _parse_date(args.date_from)
    latest = _parse_date(args.date_until) if args.date_until else earliest
    return TravelRequest(
        destination=destination.code,
        earliest_date=earliest,
        latest_date=latest,
        max_price_eur=args.budget,
        preferred_departure=TimeWindow.parse(args.depart) if args.depart else None,
        max_transfers=args.max_transfers,
        require_changeable=args.changeable,
        require_refundable=args.refundable,
        min_luggage_bags=args.bags,
        max_duration_min=round(args.max_hours * 60) if args.max_hours else None,
        passengers=args.passengers,
    )


def cmd_search(args: argparse.Namespace) -> int:
    market = load_marketplace(args.data, quarantine_dir=config.QUARANTINE_DIR).marketplace
    request = build_request(args, market)
    weights = Weights.parse(args.weights) if args.weights else Weights.default()
    result = search(market, request, weights)
    destination = market.destinations[request.destination].name
    for line in format_request(result, destination):
        print(line)
    print()
    for line in result.filtered.summary_lines():
        print(line)
    print()
    for line in format_ranking(result, args.top):
        print(line)
    return 0


def add_search_arguments(parser: argparse.ArgumentParser) -> None:
    hard = parser.add_argument_group("hard constraints (offers breaking these are removed)")
    hard.add_argument("--to", required=True, help="destination name or code, e.g. Malaga or MAL")
    hard.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD",
                      help="first acceptable travel date")
    hard.add_argument("--until", dest="date_until", metavar="YYYY-MM-DD",
                      help="last acceptable travel date (default: same as --from)")
    hard.add_argument("--budget", type=float, help="maximum ticket price in EUR")
    hard.add_argument("--max-transfers", type=int)
    hard.add_argument("--changeable", action="store_true", help="ticket must be changeable")
    hard.add_argument("--refundable", action="store_true", help="ticket must be refundable")
    hard.add_argument("--bags", type=int, help="minimum checked bags included")
    hard.add_argument("--max-hours", type=float, help="maximum journey time in hours")
    hard.add_argument("--passengers", type=int, default=1)
    soft = parser.add_argument_group("soft preferences (only affect the score)")
    soft.add_argument("--depart", help="preferred departure: early, morning, afternoon, "
                                       "evening, night or HH:MM-HH:MM")
    soft.add_argument("--weights", help="e.g. price=40,duration=25,departure=15,flexibility=20 "
                                        "(optional extra: transfers, rating, emissions)")
    parser.add_argument("--top", type=int, default=10, help="number of offers to show")


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

    search_cmd = sub.add_parser("search", help="filter and rank offers for a trip")
    add_search_arguments(search_cmd)
    search_cmd.set_defaults(func=cmd_search)
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
