"""Simulated booking: create a reference, reserve seats, save and cancel.

offers.csv is never changed. Bookings are saved in output/bookings.json, and
when the program starts every confirmed booking is subtracted from the seat
counts. That way the available seats are the same in every command, and a
booking can be cancelled without touching the offer file.

The reference is made before the seats are reserved, so a booking that fails
there takes no seat. Seats are reserved in memory first and then the file is
written. If saving
fails the reservation is undone so memory and file stay in sync. The file is
written to a temp file first and then moved into place, so a crash can't
leave a half-written bookings file.

No payment details are collected and payment_status is always simulated.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import config
from .exceptions import (
    BookingError,
    BookingNotFoundError,
    DataFileError,
)
from .models import Marketplace, Offer, RefundPolicy

logger = logging.getLogger(__name__)

# No 0/O or 1/I since they are easy to mix up.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_RE = re.compile(r"^BB-[A-HJ-NP-Z2-9]{6}$")

# Letters (including accented ones), spaces, hyphens, apostrophes and dots.
NAME_RE = re.compile(r"^[^\W\d_][^\W\d_' .-]*(?:[' .-]+[^\W\d_]+)*\.?$")

MAX_PASSENGERS = 9
REFUND_SHARE = {RefundPolicy.NONE: 0.0, RefundPolicy.PARTIAL: 0.5, RefundPolicy.FULL: 1.0}

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
PAYMENT_SIMULATED = "simulated (no real payment taken)"


@dataclass
class Booking:
    reference: str
    offer_id: str
    company: str
    destination: str
    departure: str
    arrival: str
    passenger_name: str
    passengers: int
    price_per_ticket_eur: float
    total_price_eur: float
    fare: Dict[str, Any]
    created_at: str
    status: str = CONFIRMED
    payment_status: str = PAYMENT_SIMULATED
    ranking: Dict[str, Any] = field(default_factory=dict)
    cancelled_at: Optional[str] = None
    refund_eur: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Booking":
        try:
            booking = cls(**raw)
        except TypeError as err:
            raise BookingError(f"Booking record has unexpected fields: {err}") from None
        if not REFERENCE_RE.match(str(booking.reference)):
            raise BookingError(f"Stored booking has an invalid reference {booking.reference!r}")
        if booking.status not in (CONFIRMED, CANCELLED):
            raise BookingError(f"Booking {booking.reference} has unknown status {booking.status!r}")
        return booking

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fare_snapshot(offer: Offer) -> Dict[str, Any]:
    """Copy the fare conditions so the booking keeps them even if offers change."""
    f = offer.fare
    return {
        "fare_class": f.fare_class.label,
        "change_policy": f.change_policy.value,
        "change_fee_eur": f.change_fee_eur,
        "refund_policy": f.refund_policy.value,
        "luggage_bags": f.luggage_bags,
        "summary": f.describe(),
    }


def validate_reference(reference: str) -> str:
    ref = reference.strip().upper()
    if not REFERENCE_RE.match(ref):
        raise BookingError(f"{reference!r} is not a valid booking reference (format BB-XXXXXX)")
    return ref


def validate_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not 2 <= len(cleaned) <= 60 or not NAME_RE.match(cleaned):
        raise BookingError("Passenger name must be 2-60 letters (spaces, hyphens and "
                           "apostrophes allowed)")
    return cleaned


class BookingManager:
    def __init__(self, market: Marketplace, store_path: Path = config.BOOKINGS_FILE,
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.market = market
        self.store_path = Path(store_path)
        self.rng = rng or random.SystemRandom()
        self.clock = clock
        self.bookings: Dict[str, Booking] = self._load()
        self._apply_existing_bookings()

    # Saving and loading

    def _load(self) -> Dict[str, Booking]:
        if not self.store_path.exists():
            return {}
        try:
            with open(self.store_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as err:
            raise DataFileError(f"{self.store_path.name} is not valid JSON: {err}") from None
        if not isinstance(data, dict) or not isinstance(data.get("bookings"), list):
            raise DataFileError(f"{self.store_path.name}: expected a 'bookings' list")
        bookings = {}
        for raw in data["bookings"]:
            booking = Booking.from_dict(raw)
            bookings[booking.reference] = booking
        return bookings

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".tmp")
        payload = {"bookings": [b.to_dict() for b in self.bookings.values()]}
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, self.store_path)

    def _apply_existing_bookings(self) -> None:
        """Subtract confirmed bookings from the seat counts loaded from file."""
        for booking in self.bookings.values():
            if booking.status != CONFIRMED:
                continue
            offer = self.market.get_offer(booking.offer_id)
            if offer is None:
                logger.warning("Booking %s refers to unknown offer %s", booking.reference,
                               booking.offer_id)
                continue
            if booking.passengers > offer.seats_available:
                logger.error("Booking %s exceeds remaining capacity of %s, seats set to 0",
                             booking.reference, offer.offer_id)
                offer.seats_available = 0
            else:
                offer.seats_available -= booking.passengers

    # Operations

    def _new_reference(self) -> str:
        for _ in range(100):
            ref = "BB-" + "".join(self.rng.choice(REFERENCE_ALPHABET) for _ in range(6))
            if ref not in self.bookings:
                return ref
        raise BookingError("Could not generate a unique booking reference")

    def book(self, offer_id: str, passenger_name: str, passengers: int = 1,
             ranking: Optional[Dict[str, Any]] = None) -> Booking:
        offer = self.market.get_offer(offer_id)
        if offer is None:
            raise BookingError(f"Offer {offer_id!r} does not exist")
        name = validate_name(passenger_name)
        if not 1 <= passengers <= MAX_PASSENGERS:
            raise BookingError(f"Passengers must be between 1 and {MAX_PASSENGERS}")

        reference = self._new_reference()  # before reserving, so a failure here takes no seats
        offer.reserve_seats(passengers)  # raises SeatUnavailableError
        booking = Booking(
            reference=reference,
            offer_id=offer.offer_id,
            company=offer.company.name,
            destination=offer.destination.name,
            departure=offer.departure.isoformat(timespec="minutes"),
            arrival=offer.arrival.isoformat(timespec="minutes"),
            passenger_name=name,
            passengers=passengers,
            price_per_ticket_eur=offer.price_eur,
            total_price_eur=round(offer.price_eur * passengers, 2),
            fare=fare_snapshot(offer),
            created_at=self.clock().isoformat(timespec="seconds"),
            ranking=dict(ranking or {}),
        )
        self.bookings[booking.reference] = booking
        try:
            self._save()
        except OSError as err:
            del self.bookings[booking.reference]
            offer.release_seats(passengers)
            raise BookingError(f"Booking could not be saved: {err}") from None
        logger.info("Booked %s: %s x%d for %s, %d seat(s) left", booking.reference,
                    offer.offer_id, passengers, name, offer.seats_available)
        return booking

    def get(self, reference: str) -> Booking:
        ref = validate_reference(reference)
        booking = self.bookings.get(ref)
        if booking is None:
            raise BookingNotFoundError(f"No booking with reference {ref}")
        return booking

    def cancel(self, reference: str) -> Booking:
        """Cancel a booking, release its seats and compute a simulated refund."""
        booking = self.get(reference)
        if booking.status == CANCELLED:
            raise BookingError(f"Booking {booking.reference} is already cancelled")
        refund_policy = RefundPolicy(booking.fare["refund_policy"])
        refund = round(booking.total_price_eur * REFUND_SHARE[refund_policy], 2)

        previous = (booking.status, booking.cancelled_at, booking.refund_eur)
        booking.status = CANCELLED
        booking.cancelled_at = self.clock().isoformat(timespec="seconds")
        booking.refund_eur = refund
        offer = self.market.get_offer(booking.offer_id)
        if offer is not None:
            offer.release_seats(booking.passengers)
        try:
            self._save()
        except OSError as err:
            booking.status, booking.cancelled_at, booking.refund_eur = previous
            if offer is not None:
                offer.reserve_seats(booking.passengers)
            raise BookingError(f"Cancellation could not be saved: {err}") from None
        logger.info("Cancelled %s, simulated refund EUR %.2f", booking.reference, refund)
        return booking

    def all(self) -> List[Booking]:
        return sorted(self.bookings.values(), key=lambda b: b.created_at)


def confirmation_lines(booking: Booking) -> List[str]:
    dep = datetime.fromisoformat(booking.departure)
    arr = datetime.fromisoformat(booking.arrival)
    lines = [
        f"Booking reference: {booking.reference}",
        f"Status:            {booking.status}",
        f"Passenger:         {booking.passenger_name} ({booking.passengers} seat(s))",
        f"Journey:           Madrid -> {booking.destination} with {booking.company}",
        f"Departure:         {dep:%A %d %B %Y, %H:%M}",
        f"Arrival:           {arr:%A %d %B %Y, %H:%M}",
        f"Price:             EUR {booking.total_price_eur:.2f} "
        f"({booking.passengers} x EUR {booking.price_per_ticket_eur:.2f})",
        f"Fare conditions:   {booking.fare['summary']}",
        f"Payment:           {booking.payment_status}",
        f"Offer id:          {booking.offer_id}",
    ]
    if booking.status == CANCELLED:
        lines.append(f"Cancelled:         {booking.cancelled_at}, simulated refund "
                     f"EUR {booking.refund_eur:.2f}")
    return lines
