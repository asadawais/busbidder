"""Guided mode, started with python3 -m busbidder and no arguments.

Asks one question at a time, shows which answers are hard constraints and
which are preferences, and asks again if an answer is invalid. Then it shows
the ranking and explanation and lets the traveller book. Most questions can
be skipped with Enter to use a default.

ask and say can be swapped out so the whole dialogue can be tested with
scripted answers.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple, TypeVar

from . import config
from .booking import BookingManager
from .display import format_booking, format_explanation, format_search
from .exceptions import BookingError, BusBidderError, InvalidRequestError
from .explain import explain
from .loader import load_marketplace
from .models import Marketplace
from .profiles import TravellerProfile, load_profiles
from .ranking import SearchResult, search
from .request import WINDOW_PRESETS, TimeWindow, TravelRequest
from .weights import Weights

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]
T = TypeVar("T")


class Dialogue:
    def __init__(self, ask: Optional[InputFn] = None, say: OutputFn = print) -> None:
        self.ask = ask or input
        self.say = say

    def question(self, prompt: str, parse: Callable[[str], T], default: Optional[str] = None) -> T:
        """Keep asking until parse accepts the answer. Empty input gives default."""
        suffix = f" [{default}]" if default not in (None, "") else ""
        while True:
            answer = self.ask(f"{prompt}{suffix}: ").strip()
            if not answer and default is not None:
                answer = default
            try:
                return parse(answer)
            except (BusBidderError, ValueError) as err:
                self.say(f"  {err}")

    def yes_no(self, prompt: str, default: bool = False) -> bool:
        def parse(text: str) -> bool:
            if text.lower() in ("y", "yes", "j", "ja"):
                return True
            if text.lower() in ("n", "no", "nei"):
                return False
            raise ValueError("Please answer y or n")
        return self.question(f"{prompt} (y/n)", parse, "y" if default else "n")


# Parsers that turn an answer into a value (or raise)


def _optional(parse: Callable[[str], T]) -> Callable[[str], Optional[T]]:
    return lambda text: None if text == "" else parse(text)


def _positive_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Please enter a number above 0")
    return value


def _int_between(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise ValueError(f"Please enter a whole number from {low} to {high}")
        return value
    return parse


def _date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Please use the format YYYY-MM-DD, e.g. 2027-07-12") from None


def _offer_dates(market: Marketplace) -> Tuple[date, date]:
    dates = [o.travel_date for o in market.offers.values()]
    return min(dates), max(dates)


# The dialogue


def ask_destination(d: Dialogue, market: Marketplace) -> str:
    names = sorted(market.destinations.values(), key=lambda x: x.name)
    d.say("Destinations from Madrid:")
    for i in range(0, len(names), 3):
        d.say("  " + "".join(f"{n + i + 1:>2}) {dest.name:<26}"
                             for n, dest in enumerate(names[i:i + 3])))

    def parse(text: str) -> str:
        if text.isdigit() and 1 <= int(text) <= len(names):
            return names[int(text) - 1].code
        dest = market.find_destination(text)
        if dest is None:
            raise InvalidRequestError(f"Unknown destination {text!r}")
        return dest.code

    return d.question("Destination (number, name or code)", parse)


def ask_request(d: Dialogue, market: Marketplace) -> TravelRequest:
    code = ask_destination(d, market)
    first, last = _offer_dates(market)
    d.say(f"\nOffers exist for travel between {first} and {last}.")
    earliest = d.question("First acceptable travel date", _date, first.isoformat())
    latest = d.question("Last acceptable travel date", _date, earliest.isoformat())
    passengers = d.question("Passengers", _int_between(1, 9), "1")

    d.say("\nHARD CONSTRAINTS - offers that break these are removed. Press Enter to skip.")
    budget = d.question("Maximum ticket price in EUR", _optional(_positive_float), "")
    transfers = d.question("Maximum number of transfers (0-3)",
                           _optional(_int_between(0, 3)), "")
    changeable = d.yes_no("Must the ticket be changeable?")
    refundable = d.yes_no("Must the ticket be refundable?")
    bags = d.question("Minimum checked bags (0-3)", _optional(_int_between(0, 3)), "")
    hours = d.question("Maximum journey time in hours", _optional(_positive_float), "")

    return TravelRequest(
        destination=code, earliest_date=earliest, latest_date=latest,
        max_price_eur=budget, max_transfers=transfers, require_changeable=changeable,
        require_refundable=refundable, min_luggage_bags=bags,
        max_duration_min=round(hours * 60) if hours else None, passengers=passengers,
    )


def ask_preferences(d: Dialogue, profiles: List[TravellerProfile],
                    request: TravelRequest) -> Tuple[TravelRequest, Weights, Optional[str]]:
    d.say("\nSOFT PREFERENCES - these only change the order of the offers.")
    d.say("  1) Default weights (price 40%, duration 25%, departure 15%, flexibility 20%)")
    for i, p in enumerate(profiles, start=2):
        d.say(f"  {i}) {p.name}: {p.description}")
    custom = len(profiles) + 2
    d.say(f"  {custom}) Enter my own weights")
    choice = d.question("Choose", _int_between(1, custom), "1")

    profile_id = None
    if choice == 1:
        weights = Weights.default()
    elif choice == custom:
        d.say("  Criteria: price, duration, departure, flexibility (required), "
              "transfers, rating, emissions (optional). Use percentages that total 100.")
        weights = d.question("Weights", Weights.parse,
                             "price=40,duration=25,departure=15,flexibility=20")
    else:
        profile = profiles[choice - 2]
        request = profile.apply(request)
        weights, profile_id = profile.weights, profile.profile_id
        if profile.constraints:
            d.say(f"  The {profile.profile_id} profile also adds: {profile.constraint_text()}")

    current = request.preferred_departure.label if request.preferred_departure else ""
    presets = ", ".join(WINDOW_PRESETS)
    window = d.question(f"Preferred departure ({presets}, HH:MM-HH:MM or Enter for none)",
                        _optional(TimeWindow.parse), current)
    return _with_window(request, window), weights, profile_id


def _with_window(request: TravelRequest, window: Optional[TimeWindow]) -> TravelRequest:
    return dataclasses.replace(request, preferred_departure=window)


def choose_and_book(result: SearchResult, manager: BookingManager, ask: InputFn,
                    profile_id: Optional[str], say: OutputFn = print) -> int:
    """Let the traveller pick a ranked offer and book it. Returns an exit code."""
    count = len(result.ranked)
    while True:
        answer = ask(f"\nEnter the rank (1-{count}) of the offer to book, or press Enter "
                     "to skip: ").strip()
        if not answer:
            say("No booking made.")
            return 0
        if answer.isdigit() and 1 <= int(answer) <= count:
            chosen = result.ranked[int(answer) - 1]
            break
        say(f"Please enter a number from 1 to {count}.")

    if chosen.rank > 1:
        for line in format_explanation(explain(result, chosen)):
            say(line)
    ranking = {"rank": chosen.rank, "total": chosen.total,
               "weights": result.weights.describe(), "profile": profile_id}
    while True:
        name = ask("Passenger name: ")
        try:
            booking = manager.book(chosen.offer.offer_id, name, result.request.passengers,
                                   ranking=ranking)
        except BookingError as err:
            say(f"Booking failed: {err}")
            if "name" in str(err).lower():
                continue
            return 1
        say("")
        for line in format_booking(booking):
            say(line)
        return 0


def run_interactive(data_dir: Path, profiles_file: Path, ask: Optional[InputFn] = None,
                    say: OutputFn = print) -> int:
    d = Dialogue(ask, say)
    market = load_marketplace(data_dir, quarantine_dir=config.QUARANTINE_DIR).marketplace
    manager = BookingManager(market, config.BOOKINGS_FILE)
    profiles = list(load_profiles(profiles_file).values())
    d.say("BusBidder Spain - guided search. Press Ctrl+C at any time to quit.\n")
    while True:
        request = ask_request(d, market)
        request, weights, profile_id = ask_preferences(d, profiles, request)
        result = search(market, request, weights)
        d.say("")
        for line in format_search(result, market.destinations[request.destination].name,
                                  10, profile_id):
            d.say(line)
        if result.ranked:
            choose_and_book(result, manager, d.ask, profile_id, d.say)
        if not d.yes_no("\nSearch again?"):
            d.say("Goodbye.")
            return 0
        d.say("")
