"""Shared test fixtures: small reference data and a valid offer row."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List

from busbidder.models import (
    ChangePolicy,
    Company,
    Destination,
    FareClass,
    FareConditions,
    Offer,
    RefundPolicy,
)
from busbidder.validation import OFFER_FIELDS

COMPANIES = {
    "SOL": Company("SOL", "SolRuta", "Low-cost daytime services"),
    "NOC": Company("NOC", "NocheVia", "Overnight services", reports_emissions=False),
}

DESTINATIONS = {
    "MAL": Destination("MAL", "Malaga", 530, True, "Andalusia"),
    "TOL": Destination("TOL", "Toledo", 72, False, "Castilla-La Mancha"),
}


def valid_row(**overrides: str) -> Dict[str, str]:
    """A valid SolRuta Madrid-Malaga offer. Override fields to break it."""
    row = {
        "offer_id": "SOL-MAL-20270712-0830-S",
        "company_id": "SOL",
        "destination_id": "MAL",
        "travel_date": "2027-07-12",
        "departure_time": "08:30",
        "arrival_date": "2027-07-12",
        "arrival_time": "15:45",
        "duration_min": "435",
        "price_eur": "34.99",
        "transfers": "1",
        "fare_class": "S",
        "change_policy": "fee",
        "change_fee_eur": "8.00",
        "refund_policy": "none",
        "luggage_bags": "1",
        "seats_available": "12",
        "rating": "3.8",
        "co2_kg": "20.7",
    }
    row.update(overrides)
    return row


def make_offer(offer_id: str = "SOL-MAL-20270712-0830-S", *, company: str = "SOL",
               destination: str = "MAL", departure: str = "2027-07-12 08:30",
               duration: int = 435, price: float = 34.99, transfers: int = 0,
               fare: str = "S", change: str = "fee", refund: str = "none",
               bags: int = 1, seats: int = 10, rating=4.0, co2=20.0) -> Offer:
    """Build an Offer directly (no CSV) for filter and ranking tests."""
    dep = datetime.strptime(departure, "%Y-%m-%d %H:%M")
    return Offer(
        offer_id=offer_id,
        company=COMPANIES[company],
        destination=DESTINATIONS[destination],
        departure=dep,
        arrival=dep + timedelta(minutes=duration),
        price_eur=price,
        transfers=transfers,
        fare=FareConditions(FareClass(fare), ChangePolicy(change),
                            8.0 if change == "fee" else 0.0, RefundPolicy(refund), bags),
        seats_available=seats,
        rating=rating,
        co2_kg=co2,
    )


def write_dataset(folder: Path, offer_rows: Iterable[Dict[str, str]],
                  offer_fields: List[str] = OFFER_FIELDS) -> None:
    """Write a minimal dataset (2 companies, 2 destinations) to ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    companies = {"companies": [
        {"company_id": c.company_id, "name": c.name, "profile": c.profile,
         "reports_emissions": c.reports_emissions}
        for c in COMPANIES.values()
    ]}
    (folder / "companies.json").write_text(json.dumps(companies), encoding="utf-8")
    with open(folder / "destinations.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["code", "name", "distance_km", "coastal", "region"])
        for d in DESTINATIONS.values():
            writer.writerow([d.code, d.name, d.distance_km, "yes" if d.coastal else "no", d.region])
    with open(folder / "offers.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=offer_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(offer_rows)
