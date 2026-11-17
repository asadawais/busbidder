"""Domain model.

I used plain dataclasses because they are easy to read and test.

An Offer has a Company, a Destination and a FareConditions object. The
companies don't need their own subclasses since they only differ in data
(routes, prices, schedules), not in behaviour.

Companies, destinations and fare conditions are frozen. The only thing that
changes is Offer.seats_available, and only through reserve_seats and
release_seats, so seats are always changed in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional

from .exceptions import SeatUnavailableError


class ChangePolicy(str, Enum):
    """Whether a ticket can be moved to another departure."""

    NONE = "none"  # not changeable
    FEE = "fee"  # changeable for a fee
    FREE = "free"  # changeable free of charge


class RefundPolicy(str, Enum):
    """Whether money is returned on cancellation."""

    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class FareClass(str, Enum):
    """Fare product sold by a company for a departure."""

    BASIC = "B"
    STANDARD = "S"
    FLEX = "F"

    @property
    def label(self) -> str:
        return {"B": "Basic", "S": "Standard", "F": "Flex"}[self.value]


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    profile: str
    reports_emissions: bool = True


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    distance_km: int
    coastal: bool
    region: str


@dataclass(frozen=True)
class FareConditions:
    """Ticket rules attached to an offer (and copied into a booking)."""

    fare_class: FareClass
    change_policy: ChangePolicy
    change_fee_eur: float
    refund_policy: RefundPolicy
    luggage_bags: int

    @property
    def is_changeable(self) -> bool:
        return self.change_policy is not ChangePolicy.NONE

    @property
    def is_refundable(self) -> bool:
        return self.refund_policy is not RefundPolicy.NONE

    def describe(self) -> str:
        change = {
            ChangePolicy.NONE: "no changes",
            ChangePolicy.FEE: f"changes for EUR {self.change_fee_eur:.2f}",
            ChangePolicy.FREE: "free changes",
        }[self.change_policy]
        refund = {
            RefundPolicy.NONE: "non-refundable",
            RefundPolicy.PARTIAL: "partly refundable",
            RefundPolicy.FULL: "fully refundable",
        }[self.refund_policy]
        bags = f"{self.luggage_bags} checked bag" + ("" if self.luggage_bags == 1 else "s")
        return f"{self.fare_class.label}: {change}, {refund}, {bags}"


@dataclass
class Offer:
    offer_id: str
    company: Company
    destination: Destination
    departure: datetime
    arrival: datetime
    price_eur: float
    transfers: int
    fare: FareConditions
    seats_available: int
    rating: Optional[float] = None  # 1-5 scale, None when not rated
    co2_kg: Optional[float] = None  # per passenger, None when not reported

    @property
    def travel_date(self) -> date:
        return self.departure.date()

    @property
    def duration_min(self) -> int:
        return int((self.arrival - self.departure).total_seconds() // 60)

    @property
    def is_direct(self) -> bool:
        return self.transfers == 0

    @property
    def is_sold_out(self) -> bool:
        return self.seats_available <= 0

    def reserve_seats(self, count: int = 1) -> None:
        """Take seats from the offer. This is the only place seats are reduced."""
        if count < 1:
            raise ValueError("count must be at least 1")
        if count > self.seats_available:
            raise SeatUnavailableError(
                f"Offer {self.offer_id} has {self.seats_available} seat(s) left, "
                f"{count} requested"
            )
        self.seats_available -= count

    def release_seats(self, count: int = 1) -> None:
        """Give seats back, e.g. after a simulated cancellation."""
        if count < 1:
            raise ValueError("count must be at least 1")
        self.seats_available += count

    def short_label(self) -> str:
        return (
            f"{self.company.name} {self.departure:%a %d %b %H:%M} "
            f"-> {self.destination.name} ({self.fare.fare_class.label})"
        )


@dataclass
class Marketplace:
    """All valid reference data and offers, kept in memory."""

    companies: Dict[str, Company] = field(default_factory=dict)
    destinations: Dict[str, Destination] = field(default_factory=dict)
    offers: Dict[str, Offer] = field(default_factory=dict)

    def add_offer(self, offer: Offer) -> None:
        self.offers[offer.offer_id] = offer

    def get_offer(self, offer_id: str) -> Optional[Offer]:
        return self.offers.get(offer_id)

    def all_offers(self) -> List[Offer]:
        return list(self.offers.values())

    def offers_to(self, destination_code: str) -> List[Offer]:
        return [o for o in self.offers.values() if o.destination.code == destination_code]

    def find_destination(self, name_or_code: str) -> Optional[Destination]:
        """Look up a destination by code or (case-insensitive) name."""
        key = name_or_code.strip()
        if key.upper() in self.destinations:
            return self.destinations[key.upper()]
        for dest in self.destinations.values():
            if dest.name.lower() == key.lower():
                return dest
        return None

    def offers_by_company(self) -> Dict[str, List[Offer]]:
        result: Dict[str, List[Offer]] = {cid: [] for cid in self.companies}
        for offer in self.offers.values():
            result.setdefault(offer.company.company_id, []).append(offer)
        return result
