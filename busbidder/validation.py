"""Parsing and validation of single records.

Every raw record (a dict of strings from CSV or JSON) goes through a
validator that converts the fields to the right types and checks them.

Every problem ends in one of three outcomes:

Rejected: the record is impossible or unusable (negative price, unknown
destination, arrival before departure, rating outside 1-5, bad id, duplicate
id and so on). It is left out and written to
output/quarantine/rejected_offers.csv with the reason.

Corrected: it's clear what the value should be, so it is fixed and the
record is kept (comma as decimal separator, wrong upper/lower case in a
category, duration that doesn't match the departure and arrival times). All
corrections are logged.

Review: the record is possible but unlikely (average speed above 110 km/h,
price above EUR 250). It is held back from travellers and written to
output/quarantine/review_offers.csv so someone can check it.

Offers with zero seats are valid (sold out). They are loaded and removed
later by the seat constraint, so the traveller can be told that a matching
offer exists but is full.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type, TypeVar

from . import config
from .exceptions import (
    InconsistentRecordError,
    InvalidValueError,
    MissingFieldError,
    SuspiciousRecordError,
    UnknownReferenceError,
)
from .models import (
    ChangePolicy,
    Company,
    Destination,
    FareClass,
    FareConditions,
    Offer,
    RefundPolicy,
)

# Regexes for ids and codes

# Three upper-case letters, used for both company ids (SOL) and destination
# codes (MAL).
CODE_RE = re.compile(r"^[A-Z]{3}$")

# Offer ids contain company, destination, date, departure time and fare class,
# e.g. SOL-MAL-20270712-0830-B. The named groups are used to check the id
# against the other fields in the record.
OFFER_ID_RE = re.compile(
    r"^(?P<company>[A-Z]{3})-(?P<destination>[A-Z]{3})-"
    r"(?P<date>\d{8})-(?P<time>\d{4})-(?P<fare>[BSF])$"
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# A price written with a comma as decimal separator, e.g. "24,50".
COMMA_DECIMAL_RE = re.compile(r"^\d+,\d{1,2}$")


OFFER_FIELDS = [
    "offer_id",
    "company_id",
    "destination_id",
    "travel_date",
    "departure_time",
    "arrival_date",
    "arrival_time",
    "duration_min",
    "price_eur",
    "transfers",
    "fare_class",
    "change_policy",
    "change_fee_eur",
    "refund_policy",
    "luggage_bags",
    "seats_available",
    "rating",
    "co2_kg",
]

DESTINATION_FIELDS = ["code", "name", "distance_km", "coastal", "region"]

COMPANY_FIELDS = ["company_id", "name", "profile", "reports_emissions"]

MAX_TRANSFERS = 3
MAX_LUGGAGE_BAGS = 3


class Action(str, Enum):
    REJECTED = "rejected"
    CORRECTED = "corrected"
    REVIEW = "review"


@dataclass(frozen=True)
class Issue:
    """One validation finding, used for logging and the summary report."""

    source: str
    row: int
    record_id: str
    action: Action
    field: str
    value: str
    message: str

    def __str__(self) -> str:
        return (
            f"{self.source} row {self.row} [{self.record_id}] {self.action.value}: "
            f"{self.field}={self.value!r} - {self.message}"
        )


# A correction is stored as (field, original value, message).
Correction = Tuple[str, str, str]
E = TypeVar("E", bound=Enum)


# Field parsers


def required(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if value is None or str(value).strip() == "":
        raise MissingFieldError(name, value, "required field is missing")
    return str(value).strip()


def optional(row: Mapping[str, Any], name: str) -> Optional[str]:
    value = row.get(name)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def parse_code(value: str, name: str) -> str:
    if not CODE_RE.match(value):
        raise InvalidValueError(name, value, "must be three upper-case letters")
    return value


def parse_int(value: str, name: str, minimum: Optional[int] = None,
              maximum: Optional[int] = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InvalidValueError(name, value, "not a whole number") from None
    if minimum is not None and number < minimum:
        raise InvalidValueError(name, value, f"must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise InvalidValueError(name, value, f"must be at most {maximum}")
    return number


def parse_float(value: str, name: str, corrections: List[Correction]) -> float:
    """Parse a decimal number, correcting a comma decimal separator."""
    text = value
    if COMMA_DECIMAL_RE.match(text):
        text = text.replace(",", ".")
        corrections.append((name, value, f"comma decimal separator corrected to {text}"))
    try:
        return float(text)
    except ValueError:
        raise InvalidValueError(name, value, "not a number") from None


def parse_date(value: str, name: str) -> date:
    if not DATE_RE.match(value):
        raise InvalidValueError(name, value, "date must use YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidValueError(name, value, "not a real calendar date") from None


def parse_time(value: str, name: str) -> time:
    if not TIME_RE.match(value):
        raise InvalidValueError(name, value, "time must use HH:MM")
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise InvalidValueError(name, value, "not a real clock time") from None


def parse_enum(value: str, enum_cls: Type[E], name: str,
               corrections: List[Correction]) -> E:
    """Match a category value, correcting harmless case differences."""
    try:
        return enum_cls(value)
    except ValueError:
        pass
    normalised = value.strip().lower()
    for member in enum_cls:
        if str(member.value).lower() == normalised:
            corrections.append((name, value, f"normalised to {member.value!r}"))
            return member
    allowed = ", ".join(str(m.value) for m in enum_cls)
    raise InvalidValueError(name, value, f"must be one of: {allowed}")


def parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    raise InvalidValueError(name, value, "must be true/false or yes/no")


# Validators for reference data


def validate_company(raw: Mapping[str, Any]) -> Company:
    company_id = parse_code(required(raw, "company_id"), "company_id")
    reports = raw.get("reports_emissions", True)
    return Company(
        company_id=company_id,
        name=required(raw, "name"),
        profile=optional(raw, "profile") or "",
        reports_emissions=parse_bool(reports, "reports_emissions"),
    )


def validate_destination(raw: Mapping[str, Any]) -> Destination:
    return Destination(
        code=parse_code(required(raw, "code"), "code"),
        name=required(raw, "name"),
        distance_km=parse_int(required(raw, "distance_km"), "distance_km", minimum=1),
        coastal=parse_bool(required(raw, "coastal"), "coastal"),
        region=optional(raw, "region") or "",
    )


# Offer validator


class OfferValidator:
    """Turns a raw offer row into an Offer or raises a RecordError.

    The checks always run in the same order: ids and references, then types and
    ranges, then fields that must agree with each other, and plausibility last.
    That way a record only goes to review if everything else is fine.
    """

    def __init__(self, companies: Mapping[str, Company],
                 destinations: Mapping[str, Destination]) -> None:
        self.companies = companies
        self.destinations = destinations

    def validate(self, row: Mapping[str, Any]) -> Tuple[Offer, List[Correction]]:
        corrections: List[Correction] = []

        # Identifiers and references
        offer_id = required(row, "offer_id")
        id_match = OFFER_ID_RE.match(offer_id)
        if not id_match:
            raise InvalidValueError(
                "offer_id", offer_id, "does not match COMPANY-DEST-YYYYMMDD-HHMM-FARE"
            )

        company_id = parse_code(required(row, "company_id"), "company_id")
        company = self.companies.get(company_id)
        if company is None:
            raise UnknownReferenceError("company_id", company_id, "unknown company")

        destination_id = parse_code(required(row, "destination_id"), "destination_id")
        destination = self.destinations.get(destination_id)
        if destination is None:
            raise UnknownReferenceError("destination_id", destination_id, "unknown destination")

        # Dates and times
        travel_date = parse_date(required(row, "travel_date"), "travel_date")
        departure = datetime.combine(
            travel_date, parse_time(required(row, "departure_time"), "departure_time")
        )
        arrival_date = parse_date(required(row, "arrival_date"), "arrival_date")
        arrival = datetime.combine(
            arrival_date, parse_time(required(row, "arrival_time"), "arrival_time")
        )

        fare_class = parse_enum(required(row, "fare_class"), FareClass, "fare_class", corrections)

        # The id must agree with the record's own fields
        expected_parts = {
            "company": company_id,
            "destination": destination_id,
            "date": f"{travel_date:%Y%m%d}",
            "time": f"{departure:%H%M}",
            "fare": fare_class.value,
        }
        for part, expected in expected_parts.items():
            if id_match.group(part) != expected:
                raise InconsistentRecordError(
                    "offer_id", offer_id,
                    f"{part} part {id_match.group(part)!r} does not match record value {expected!r}",
                )

        if arrival <= departure:
            raise InconsistentRecordError(
                "arrival", f"{arrival:%Y-%m-%d %H:%M}", "arrival is not after departure"
            )

        computed_duration = int((arrival - departure).total_seconds() // 60)
        stated_raw = required(row, "duration_min")
        stated_duration = parse_int(stated_raw, "duration_min", minimum=1)
        if abs(stated_duration - computed_duration) > config.DURATION_TOLERANCE_MIN:
            corrections.append((
                "duration_min", stated_raw,
                f"disagrees with departure/arrival times, corrected to {computed_duration}",
            ))

        # Price, capacity and other numbers
        price = parse_float(required(row, "price_eur"), "price_eur", corrections)
        if price <= 0:
            raise InvalidValueError("price_eur", row.get("price_eur"), "price must be positive")
        price = round(price, 2)

        transfers = parse_int(required(row, "transfers"), "transfers", 0, MAX_TRANSFERS)
        seats = parse_int(required(row, "seats_available"), "seats_available", minimum=0)
        luggage = parse_int(required(row, "luggage_bags"), "luggage_bags", 0, MAX_LUGGAGE_BAGS)

        # Fare conditions
        change_policy = parse_enum(
            required(row, "change_policy"), ChangePolicy, "change_policy", corrections
        )
        refund_policy = parse_enum(
            required(row, "refund_policy"), RefundPolicy, "refund_policy", corrections
        )
        fee_raw = optional(row, "change_fee_eur")
        if change_policy is ChangePolicy.FEE:
            if fee_raw is None:
                raise MissingFieldError("change_fee_eur", fee_raw, "required when change_policy is 'fee'")
            change_fee = parse_float(fee_raw, "change_fee_eur", corrections)
            if change_fee < 0:
                raise InvalidValueError("change_fee_eur", fee_raw, "fee cannot be negative")
        else:
            change_fee = 0.0
            if fee_raw is not None and parse_float(fee_raw, "change_fee_eur", []) != 0:
                corrections.append((
                    "change_fee_eur", fee_raw,
                    f"fee ignored because change_policy is {change_policy.value!r}",
                ))

        fare = FareConditions(
            fare_class=fare_class,
            change_policy=change_policy,
            change_fee_eur=round(change_fee, 2),
            refund_policy=refund_policy,
            luggage_bags=luggage,
        )

        # Optional values: can be missing, the scoring handles that later
        rating: Optional[float] = None
        rating_raw = optional(row, "rating")
        if rating_raw is not None:
            rating = parse_float(rating_raw, "rating", corrections)
            if not config.RATING_MIN <= rating <= config.RATING_MAX:
                raise InvalidValueError(
                    "rating", rating_raw,
                    f"outside the {config.RATING_MIN:g}-{config.RATING_MAX:g} scale",
                )

        co2: Optional[float] = None
        co2_raw = optional(row, "co2_kg")
        if co2_raw is not None:
            co2 = parse_float(co2_raw, "co2_kg", corrections)
            if co2 < 0:
                raise InvalidValueError("co2_kg", co2_raw, "emissions cannot be negative")

        # Plausibility checks (possible, but someone should look at it)
        hours = computed_duration / 60
        speed = destination.distance_km / hours
        if speed > config.MAX_PLAUSIBLE_SPEED_KMH:
            raise SuspiciousRecordError(
                "duration_min", computed_duration,
                f"implies an average speed of {speed:.0f} km/h "
                f"(limit {config.MAX_PLAUSIBLE_SPEED_KMH:.0f} km/h)",
            )
        if price > config.MAX_PLAUSIBLE_PRICE_EUR:
            raise SuspiciousRecordError(
                "price_eur", price,
                f"above the plausibility limit of EUR {config.MAX_PLAUSIBLE_PRICE_EUR:.0f}",
            )

        offer = Offer(
            offer_id=offer_id,
            company=company,
            destination=destination,
            departure=departure,
            arrival=arrival,
            price_eur=price,
            transfers=transfers,
            fare=fare,
            seats_available=seats,
            rating=rating,
            co2_kg=co2,
        )
        return offer, corrections


def issue_from_correction(source: str, row_no: int, record_id: str,
                          correction: Correction) -> Issue:
    field, value, message = correction
    return Issue(source, row_no, record_id, Action.CORRECTED, field, str(value), message)


def issues_by_action(issues: List[Issue]) -> Dict[Action, int]:
    counts = {action: 0 for action in Action}
    for issue in issues:
        counts[issue.action] += 1
    return counts
