"""Seeded generator for the simulated dataset.

Run with:  python -m busbidder.data_generator --seed 42

The same seed always gives byte-identical files, so every ranking in the
report can be reproduced.

How the companies differ (see the README and the report):

* SolRuta        - cheapest per km, daytime only, slower, often one transfer,
                   Basic fares can't be changed.
* Camino Express - most expensive and fastest, direct to the big cities,
                   only Standard and Flex fares.
* CostaLink      - cheaper and direct on the coast with more departures there,
                   one or two transfers to inland cities.
* VerdeBus       - low-emission fleet (lowest CO2), free changes on all fares,
                   mid price.
* NocheVia       - evening/overnight, slow, more luggage, doesn't report
                   emissions (CO2 left empty on purpose).

A few faulty records are added to offers.csv on purpose to test the
validation. injected_faults.json lists them with the expected result and the
tests check the loader against that list.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .validation import OFFER_FIELDS

# code, name, road distance from Madrid (km, approximate), coastal, region
DESTINATIONS: List[Tuple[str, str, int, bool, str]] = [
    ("BAR", "Barcelona", 620, True, "Catalonia"),
    ("VAL", "Valencia", 360, True, "Valencian Community"),
    ("SEV", "Seville", 530, False, "Andalusia"),
    ("MAL", "Malaga", 530, True, "Andalusia"),
    ("GRA", "Granada", 420, False, "Andalusia"),
    ("COR", "Cordoba", 400, False, "Andalusia"),
    ("BIL", "Bilbao", 400, True, "Basque Country"),
    ("SSB", "San Sebastian", 450, True, "Basque Country"),
    ("ZAR", "Zaragoza", 315, False, "Aragon"),
    ("ALI", "Alicante", 420, True, "Valencian Community"),
    ("MUR", "Murcia", 400, False, "Region of Murcia"),
    ("SAL", "Salamanca", 215, False, "Castile and Leon"),
    ("TOL", "Toledo", 72, False, "Castilla-La Mancha"),
    ("SCQ", "Santiago de Compostela", 600, False, "Galicia"),
    ("CAD", "Cadiz", 650, True, "Andalusia"),
]

MAJOR_CITIES = {"BAR", "VAL", "SEV", "MAL", "BIL", "ZAR"}

TRAVEL_START = date(2027, 7, 10)
TRAVEL_DAYS = 7  # 10-16 July 2027


@dataclass(frozen=True)
class CompanySpec:
    company_id: str
    name: str
    profile: str
    rate_per_km: float
    speed_kmh: float
    slots: Tuple[str, ...]
    departures_per_day: Tuple[int, int]
    fare_classes: Tuple[str, ...]
    rating_mean: float
    co2_g_per_km: Optional[float]  # None = company does not report emissions


COMPANIES: List[CompanySpec] = [
    CompanySpec("SOL", "SolRuta", "Low-cost daytime services", 0.050, 74,
                ("07:00", "08:30", "10:00", "12:30", "15:00", "17:00"), (1, 2),
                ("B", "S"), 3.7, 34),
    CompanySpec("CAM", "Camino Express", "Fast direct connections between major cities",
                0.092, 86, ("06:30", "08:00", "11:00", "14:00", "18:00", "20:00"), (2, 2),
                ("S", "F"), 4.3, 29),
    CompanySpec("CST", "CostaLink", "Strong coverage of coastal destinations", 0.072, 78,
                ("07:30", "09:30", "13:00", "16:30", "19:00"), (1, 2), ("B", "S"), 3.9, 30),
    CompanySpec("VER", "VerdeBus", "Environment-focused services and modern vehicles",
                0.082, 78, ("08:00", "10:30", "13:30", "16:00", "18:30"), (1, 2),
                ("S", "F"), 4.3, 11),
    CompanySpec("NOC", "NocheVia", "Overnight and late-evening services", 0.056, 68,
                ("20:30", "21:30", "22:15", "23:30"), (1, 1), ("B", "S"), 3.7, None),
]

# (company, fare class) -> change policy, change fee, refund policy, checked bags
FARE_RULES: Dict[Tuple[str, str], Tuple[str, float, str, int]] = {
    ("SOL", "B"): ("none", 0, "none", 0),
    ("SOL", "S"): ("fee", 8, "none", 1),
    ("CAM", "S"): ("fee", 10, "none", 1),
    ("CAM", "F"): ("free", 0, "full", 1),
    ("CST", "B"): ("none", 0, "none", 1),
    ("CST", "S"): ("fee", 5, "partial", 1),
    ("VER", "S"): ("free", 0, "none", 1),
    ("VER", "F"): ("free", 0, "full", 2),
    ("NOC", "B"): ("none", 0, "none", 1),
    ("NOC", "S"): ("fee", 6, "partial", 2),
}

FARE_PRICE_FACTOR = {"B": 1.00, "S": 1.25, "F": 1.55}


def _round_price(value: float) -> float:
    """Round to a price ending in .49 or .99 like real ticket prices."""
    return max(4.99, round(value * 2) / 2 - 0.01)


def _transfers(spec: CompanySpec, code: str, distance: int, coastal: bool,
               rng: random.Random) -> int:
    if distance < 150:
        return 0
    if spec.company_id == "CAM":
        return 0 if code in MAJOR_CITIES else int(rng.random() < 0.5)
    if spec.company_id == "CST":
        if coastal:
            return int(rng.random() < 0.1)
        return 2 if distance > 400 and rng.random() < 0.4 else 1
    if spec.company_id == "SOL":
        return int(rng.random() < 0.35)
    return int(rng.random() < 0.3)  # VER, NOC


def _price(spec: CompanySpec, distance: int, coastal: bool, fare: str,
           departure: datetime, transfers: int, rng: random.Random) -> float:
    rate = spec.rate_per_km
    if spec.company_id == "CST" and coastal:
        rate -= 0.006  # cheaper on its strong coastal network
    base = 3.5 + distance * rate
    base *= FARE_PRICE_FACTOR[fare]
    if departure.weekday() >= 4:  # Friday-Sunday demand
        base *= 1.10
    if departure.hour in (7, 8, 17, 18):  # peak hours
        base *= 1.08
    base *= 1 - 0.08 * transfers  # indirect journeys are discounted
    base *= rng.uniform(0.92, 1.10)
    return _round_price(base)


def generate_rows(seed: int = config.DEFAULT_SEED) -> List[Dict[str, str]]:
    """Generate clean offer rows (all values as strings, like a CSV)."""
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for spec in COMPANIES:
        for code, _name, distance, coastal, _region in DESTINATIONS:
            for day in range(TRAVEL_DAYS):
                travel_date = TRAVEL_START + timedelta(days=day)
                low, high = spec.departures_per_day
                count = rng.randint(low, high)
                if spec.company_id == "CST":
                    count = 2 if coastal else 1
                slots = sorted(rng.sample(spec.slots, count))
                for slot in slots:
                    departure = datetime.combine(
                        travel_date, datetime.strptime(slot, "%H:%M").time()
                    )
                    transfers = _transfers(spec, code, distance, coastal, rng)
                    minutes = distance / spec.speed_kmh * 60 + 15
                    minutes += sum(rng.randint(40, 75) for _ in range(transfers))
                    if spec.company_id == "NOC" and distance >= 150:
                        minutes += rng.randint(30, 90)  # overnight rest stops
                    duration = int(round(minutes / 5) * 5)
                    arrival = departure + timedelta(minutes=duration)
                    rating = min(5.0, max(1.0, rng.gauss(spec.rating_mean, 0.2)))
                    seats = 0 if rng.random() < 0.04 else rng.randint(1, 45)

                    for fare in spec.fare_classes:
                        change, fee, refund, bags = FARE_RULES[(spec.company_id, fare)]
                        price = _price(spec, distance, coastal, fare, departure, transfers, rng)
                        co2 = ""
                        if spec.co2_g_per_km is not None:
                            co2 = f"{distance * spec.co2_g_per_km / 1000 * (1 + 0.15 * transfers):.1f}"
                        rows.append({
                            "offer_id": f"{spec.company_id}-{code}-{travel_date:%Y%m%d}-"
                                        f"{departure:%H%M}-{fare}",
                            "company_id": spec.company_id,
                            "destination_id": code,
                            "travel_date": f"{travel_date:%Y-%m-%d}",
                            "departure_time": f"{departure:%H:%M}",
                            "arrival_date": f"{arrival:%Y-%m-%d}",
                            "arrival_time": f"{arrival:%H:%M}",
                            "duration_min": str(duration),
                            "price_eur": f"{price:.2f}",
                            "transfers": str(transfers),
                            "fare_class": fare,
                            "change_policy": change,
                            "change_fee_eur": f"{fee:.2f}" if change == "fee" else "",
                            "refund_policy": refund,
                            "luggage_bags": str(bags),
                            # Seats belong to the departure, but each fare class
                            # has its own quota in this simulation.
                            "seats_available": str(seats if fare == spec.fare_classes[0]
                                                   else (0 if seats == 0 else rng.randint(1, 20))),
                            "rating": f"{rating:.1f}",
                            "co2_kg": co2,
                        })
    return rows


def _set_arrival(row: Dict[str, str], minutes: int) -> None:
    departure = datetime.strptime(f"{row['travel_date']} {row['departure_time']}",
                                  "%Y-%m-%d %H:%M")
    arrival = departure + timedelta(minutes=minutes)
    row["arrival_date"] = f"{arrival:%Y-%m-%d}"
    row["arrival_time"] = f"{arrival:%H:%M}"
    row["duration_min"] = str(minutes)


def inject_faults(rows: List[Dict[str, str]], seed: int) -> List[Dict[str, str]]:
    """Turn a few rows into faulty records and return a list of what was changed."""
    rng = random.Random(seed + 1)
    distance = {code: dist for code, _n, dist, _c, _r in DESTINATIONS}
    pool = list(range(len(rows)))
    rng.shuffle(pool)
    manifest: List[Dict[str, str]] = []

    def take(predicate=lambda r: True) -> Dict[str, str]:
        for i, idx in enumerate(pool):
            if predicate(rows[idx]):
                return rows[pool.pop(i)]
        raise RuntimeError("no row available for fault injection")

    def note(row: Dict[str, str], fault: str, action: str) -> None:
        manifest.append({"offer_id": row["offer_id"], "fault": fault,
                         "expected_action": action})

    row = take(); row["price_eur"] = "-" + row["price_eur"]
    note(row, "negative price", "rejected")

    row = take(); row["destination_id"] = "XYZ"
    note(row, "unknown destination", "rejected")

    row = take(); row["company_id"] = "ABC"
    note(row, "unknown company", "rejected")

    row = take(); row["travel_date"] = row["travel_date"][:8] + "32"
    note(row, "invalid date", "rejected")

    row = take(lambda r: int(r["departure_time"][:2]) >= 6)
    dep = datetime.strptime(row["departure_time"], "%H:%M")
    row["arrival_date"] = row["travel_date"]
    row["arrival_time"] = f"{dep - timedelta(hours=2):%H:%M}"
    note(row, "arrival before departure", "rejected")

    row = take(); row["rating"] = "7.5"
    note(row, "rating outside 1-5 scale", "rejected")

    row = take(); row["seats_available"] = "-3"
    note(row, "negative seat count", "rejected")

    row = take(); row["price_eur"] = ""
    note(row, "missing price", "rejected")

    row = take(); row["offer_id"] = row["offer_id"].replace("-", "_")
    note(row, "malformed offer id", "rejected")

    row = take(); row["destination_id"] = "TOL" if row["destination_id"] != "TOL" else "BAR"
    note(row, "offer id does not match destination", "rejected")

    row = take(); row["refund_policy"] = "maybe"
    note(row, "invalid refund category", "rejected")

    duplicate = dict(take())
    rows.append(duplicate)
    note(duplicate, "duplicate offer id", "rejected")

    row = take(); row["duration_min"] = str(int(row["duration_min"]) + 45)
    note(row, "stated duration disagrees with times", "corrected")

    row = take(); row["price_eur"] = row["price_eur"].replace(".", ",")
    note(row, "comma decimal separator in price", "corrected")

    row = take(); row["change_policy"] = row["change_policy"].upper()
    note(row, "upper-case category value", "corrected")

    row = take(lambda r: distance[r["destination_id"]] >= 400)
    _set_arrival(row, 150)
    note(row, "implausibly fast journey", "review")

    row = take(); row["price_eur"] = "389.00"
    note(row, "extreme price", "review")

    for _ in range(3):
        row = take(); row["rating"] = ""
        note(row, "missing rating (optional field)", "accepted")

    return manifest


def write_dataset(out_dir: Path, seed: int = config.DEFAULT_SEED,
                  with_faults: bool = True) -> Dict[str, int]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    companies = {"companies": [
        {"company_id": c.company_id, "name": c.name, "profile": c.profile,
         "reports_emissions": c.co2_g_per_km is not None}
        for c in COMPANIES
    ]}
    with open(out_dir / "companies.json", "w", encoding="utf-8") as handle:
        json.dump(companies, handle, indent=2)
        handle.write("\n")

    with open(out_dir / "destinations.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["code", "name", "distance_km", "coastal", "region"])
        for code, name, dist, coastal, region in DESTINATIONS:
            writer.writerow([code, name, dist, "yes" if coastal else "no", region])

    rows = generate_rows(seed)
    manifest: List[Dict[str, str]] = inject_faults(rows, seed) if with_faults else []

    with open(out_dir / "offers.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OFFER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    with open(out_dir / "injected_faults.json", "w", encoding="utf-8") as handle:
        json.dump({"seed": seed, "faults": manifest}, handle, indent=2)
        handle.write("\n")

    return {"companies": len(COMPANIES), "destinations": len(DESTINATIONS),
            "offers": len(rows), "faults": len(manifest)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the BusBidder Spain dataset.")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=config.DATA_DIR)
    parser.add_argument("--clean", action="store_true", help="do not inject faulty records")
    args = parser.parse_args(argv)
    counts = write_dataset(args.out, args.seed, with_faults=not args.clean)
    print(f"Wrote {counts['offers']} offers ({counts['faults']} injected faults), "
          f"{counts['companies']} companies and {counts['destinations']} destinations "
          f"to {args.out} using seed {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
