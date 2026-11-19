"""Command-line interface.

Running python3 -m busbidder without a command starts the guided mode
(interactive.py). The commands below are for direct use and scripting:

validate   check the data files and show rejected, corrected and held back records
search     filter and rank offers for one trip, explain the top one, optionally book
compare    run the same trip for several traveller profiles
book       book an offer by id
bookings   list bookings or show one
cancel     cancel a booking (simulated refund)
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .booking import BookingManager
from .display import (
    format_booking,
    format_booking_list,
    format_compare,
    format_search,
)
from .exceptions import BusBidderError, InvalidRequestError
from .interactive import InputFn, choose_and_book, run_interactive
from .loader import load_marketplace
from .logging_setup import configure_logging
from .models import Marketplace
from .profiles import compare_profiles, load_profiles, select_profiles
from .ranking import search
from .request import TimeWindow, TravelRequest
from .validation import Action
from .weights import Weights

def _print(lines) -> None:
    for line in lines:
        print(line)


def _open(args: argparse.Namespace):
    """Load the marketplace and apply existing bookings to the seat counts."""
    market = load_marketplace(args.data, quarantine_dir=config.QUARANTINE_DIR).marketplace
    manager = BookingManager(market, config.BOOKINGS_FILE)
    return market, manager


def _parse_date(text: str):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidRequestError(f"Date {text!r} must use YYYY-MM-DD") from None


def _hours_to_minutes(hours: Optional[float]) -> Optional[int]:
    if hours is None:
        return None
    if not math.isfinite(hours) or hours <= 0:
        raise InvalidRequestError("Maximum journey time must be a positive number of hours")
    return round(hours * 60)


def build_request(args: argparse.Namespace, market: Marketplace) -> TravelRequest:
    destination = market.find_destination(args.to)
    if destination is None:
        names = ", ".join(sorted(d.name for d in market.destinations.values()))
        raise InvalidRequestError(f"Unknown destination {args.to!r}. Choose from: {names}")
    earliest = _parse_date(args.date_from)
    latest = _parse_date(args.date_until) if args.date_until else earliest
    depart = getattr(args, "depart", None)
    return TravelRequest(
        destination=destination.code,
        earliest_date=earliest,
        latest_date=latest,
        max_price_eur=args.budget,
        preferred_departure=TimeWindow.parse(depart) if depart else None,
        max_transfers=args.max_transfers,
        require_changeable=args.changeable,
        require_refundable=args.refundable,
        min_luggage_bags=args.bags,
        max_duration_min=_hours_to_minutes(args.max_hours),
        passengers=args.passengers,
    )


# Commands


def cmd_validate(args: argparse.Namespace) -> int:
    result = load_marketplace(args.data, quarantine_dir=config.QUARANTINE_DIR)
    report = result.report
    market = result.marketplace
    print(f"Companies: {len(market.companies)}   Destinations: {len(market.destinations)}")
    _print(report.summary_lines())
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


def cmd_search(args: argparse.Namespace, ask: Optional[InputFn] = None) -> int:
    ask = ask or input  # looked up at call time so tests can replace input()
    market, manager = _open(args)
    request = build_request(args, market)
    profile_id = None
    if args.profile:
        if args.weights:
            raise InvalidRequestError("Use either --profile or --weights, not both")
        profile = select_profiles(load_profiles(args.profiles_file), [args.profile])[0]
        request = profile.apply(request)
        if args.depart:  # an explicit --depart overrides the profile's window
            request = dataclasses.replace(request,
                                          preferred_departure=TimeWindow.parse(args.depart))
        weights, profile_id = profile.weights, profile.profile_id
    else:
        weights = Weights.parse(args.weights) if args.weights else Weights.default()

    result = search(market, request, weights)
    destination = market.destinations[request.destination].name
    _print(format_search(result, destination, args.top, profile_id))
    if args.book and result.ranked:
        return choose_and_book(result, manager, ask, profile_id)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    market, _ = _open(args)
    base = build_request(args, market)
    ids = [i.strip() for i in args.profiles.split(",")] if args.profiles else None
    profiles = select_profiles(load_profiles(args.profiles_file), ids)
    results = compare_profiles(market, base, profiles)
    destination = market.destinations[base.destination].name
    print("Base trip (hard constraints shared by every profile):")
    _print(f"  - {line}" for line in base.hard_constraint_lines(destination))
    print()
    _print(format_compare(results, destination, args.top))
    return 0


def cmd_book(args: argparse.Namespace) -> int:
    _, manager = _open(args)
    booking = manager.book(args.offer_id.strip().upper(), args.name, args.passengers)
    _print(format_booking(booking))
    return 0


def cmd_bookings(args: argparse.Namespace) -> int:
    _, manager = _open(args)
    if args.reference:
        _print(format_booking(manager.get(args.reference)))
    else:
        _print(format_booking_list(manager.all()))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    _, manager = _open(args)
    booking = manager.cancel(args.reference)
    _print(format_booking(booking))
    return 0


# Argument parsing


def add_trip_arguments(parser: argparse.ArgumentParser) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m busbidder",
        description="BusBidder Spain - filter, rank and book bus offers from Madrid.",
    )
    parser.add_argument("--data", type=Path, default=config.DATA_DIR,
                        help="folder containing companies.json, destinations.csv, offers.csv")
    parser.add_argument("--profiles-file", type=Path, default=config.DATA_DIR / "profiles.json")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="load and validate the data files")
    validate.add_argument("-v", "--verbose", action="store_true",
                          help="list every rejected, corrected and review record")
    validate.set_defaults(func=cmd_validate)

    search_cmd = sub.add_parser("search", help="filter, rank and explain offers for a trip")
    add_trip_arguments(search_cmd)
    soft = search_cmd.add_argument_group("soft preferences (only affect the score)")
    soft.add_argument("--depart", help="preferred departure: early, morning, afternoon, "
                                       "evening, night or HH:MM-HH:MM")
    soft.add_argument("--weights", help="e.g. price=40,duration=25,departure=15,flexibility=20 "
                                        "(optional extra: transfers, rating, emissions)")
    soft.add_argument("--profile", help="use a traveller profile's weights, departure window "
                                        "and extra constraints (budget, business, flexible, eco)")
    search_cmd.add_argument("--top", type=int, default=10, help="number of offers to show")
    search_cmd.add_argument("--book", action="store_true",
                            help="choose an offer from the ranking and book it")
    search_cmd.set_defaults(func=cmd_search)

    compare = sub.add_parser("compare", help="compare traveller profiles on the same trip")
    add_trip_arguments(compare)
    compare.add_argument("--profiles", help="comma-separated profile ids (default: all)")
    compare.add_argument("--top", type=int, default=3, help="offers shown per profile")
    compare.set_defaults(func=cmd_compare)

    book = sub.add_parser("book", help="book an offer by id")
    book.add_argument("offer_id", help="e.g. SOL-MAL-20270714-1000-S")
    book.add_argument("--name", required=True, help="passenger name")
    book.add_argument("--passengers", type=int, default=1)
    book.set_defaults(func=cmd_book)

    bookings = sub.add_parser("bookings", help="list bookings, or show one by reference")
    bookings.add_argument("reference", nargs="?", help="e.g. BB-7K3Q9X")
    bookings.set_defaults(func=cmd_bookings)

    cancel = sub.add_parser("cancel", help="cancel a booking (simulated refund)")
    cancel.add_argument("reference")
    cancel.set_defaults(func=cmd_cancel)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    configure_logging(config.LOG_FILE)
    if not argv:
        try:
            return run_interactive(config.DATA_DIR, config.DATA_DIR / "profiles.json")
        except BusBidderError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BusBidderError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
