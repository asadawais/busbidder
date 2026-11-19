"""Text formatting for the command line.

Kept apart from the CLI so another interface (like a web page) could reuse
the search and ranking code without the printing.
"""

from __future__ import annotations

from typing import List, Optional

from .booking import Booking, confirmation_lines
from .criteria import get_criterion
from .explain import explain
from .profiles import ProfileResult, cross_ranks
from .ranking import SearchResult, criterion_influence


SHORT_LABELS = {
    "price": "Price",
    "duration": "Dur",
    "departure": "DepTime",
    "flexibility": "Flex",
    "transfers": "Transf",
    "rating": "Rating",
    "emissions": "CO2",
}


def format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins:02d}"


def format_request(result: SearchResult, destination_name: str) -> List[str]:
    lines = ["HARD CONSTRAINTS (offers that break these are removed)"]
    lines += [f"  - {line}" for line in result.request.hard_constraint_lines(destination_name)]
    lines.append("SOFT PREFERENCES (only affect the score)")
    lines += [f"  - {line}" for line in result.request.preference_lines()]
    lines.append(f"  - Weights: {result.weights.describe()}")
    for warning in result.weights.warnings:
        lines.append(f"  ! Warning: {warning}")
    return lines


def format_ranking(result: SearchResult, top: int = 10) -> List[str]:
    if not result.ranked:
        return ["No offer satisfies all hard constraints. Try relaxing the constraint "
                "that removed the most offers (see the summary above)."]
    names = list(result.weights)
    short = {name: SHORT_LABELS.get(name, name[:6]) for name in names}
    header = (f"{'#':>3}  {'Company':<15}{'Date':<11}{'Dep':<6}{'Arr':<9}{'Time':<6}"
              f"{'EUR':>7} {'Tr':>2}  {'Fare':<9}"
              + "".join(f"{short[n]:>8}" for n in names) + f"{'TOTAL':>8}")
    lines = [header, "-" * len(header)]
    for item in result.ranked[:top]:
        o = item.offer
        arrival = f"{o.arrival:%H:%M}" + ("+1" if o.arrival.date() > o.travel_date else "")
        day = f"{o.departure:%a %d}"
        row = (f"{item.rank:>3}  {o.company.name:<15}{day:<11}"
               f"{o.departure:%H:%M} {arrival:<9}{format_duration(o.duration_min):<6}"
               f"{o.price_eur:>7.2f} {o.transfers:>2}  {o.fare.fare_class.label:<9}")
        for name in names:
            score = item.scores[name]
            cell = f"{score.score:.0f}" + ("*" if score.missing else "")
            row += f"{cell:>8}"
        row += f"{item.total:>8.2f}"
        if item.tied:
            row += "  (tie)"
        lines.append(row)
    if len(result.ranked) > top:
        lines.append(f"... {len(result.ranked) - top} more offer(s) not shown (use --top)")
    legend = ", ".join(f"{short[n]} = {get_criterion(n).label.lower()}" for n in names)
    lines.append(f"Scores are 0-100 per criterion ({legend}). TOTAL is the weighted sum. "
                 "+1 = arrives next day.")
    if any(s.missing for item in result.ranked[:top] for s in item.scores.values()):
        lines.append("* = value missing in the data. The score shown comes from the "
                     "missing-value policy.")

    influence = criterion_influence(result.ranked)
    if influence:
        parts = ", ".join(f"{name} {share:.0%}" for name, share in
                          sorted(influence.items(), key=lambda kv: -kv[1]))
        lines.append(f"What actually separated the offers: {parts}")
    return lines


def format_search(result: SearchResult, destination_name: str, top: int = 10,
                  profile_id: Optional[str] = None) -> List[str]:
    """Everything printed for one search: request, filter summary, ranking, explanation."""
    lines: List[str] = [f"Profile: {profile_id}"] if profile_id else []
    lines += format_request(result, destination_name) + [""]
    lines += result.filtered.summary_lines() + [""]
    lines += format_ranking(result, top) + [""]
    lines += format_explanation(explain(result))
    return lines


def format_explanation(lines: List[str]) -> List[str]:
    if not lines:
        return []
    return ["WHY THIS RECOMMENDATION"] + [f"  {line}" for line in lines]


def format_compare(results: List[ProfileResult], destination_name: str,
                   top: int = 3) -> List[str]:
    lines: List[str] = []
    for item in results:
        profile, result = item.profile, item.result
        lines.append("=" * 78)
        lines.append(f"{profile.name.upper()} ({profile.profile_id})")
        lines.append(f"  {profile.description}")
        window = profile.preferred_departure or "none"
        lines.append(f"  Weights: {profile.weights.describe()}")
        lines.append(f"  Preferred departure: {window}. Extra hard constraints: "
                     f"{profile.constraint_text()}.")
        lines.append(f"  Acceptable offers: {len(result.ranked)} of "
                     f"{result.filtered.in_scope} on this route and dates")
        lines.append("")
        lines += ["  " + line for line in format_ranking(result, top)[:-2]]
        lines.append("")
        lines += ["  " + line for line in explain(result)[:-1]]
        lines.append("")

    lines.append("=" * 78)
    lines.append(f"SUMMARY: recommended offer per profile (Madrid -> {destination_name})")
    header = (f"{'Profile':<11}{'Company':<16}{'Date':<8}{'Dep':<6}{'Time':<6}"
              f"{'EUR':>7} {'Tr':>2}  {'Fare':<9}{'CO2':>6}{'Total':>8}")
    lines += [header, "-" * len(header)]
    winners = set()
    for item in results:
        best = item.result.best
        if best is None:
            lines.append(f"{item.profile.profile_id:<11}no acceptable offer")
            continue
        o = best.offer
        winners.add(o.offer_id)
        co2 = "n/a" if o.co2_kg is None else f"{o.co2_kg:.1f}"
        lines.append(f"{item.profile.profile_id:<11}{o.company.name:<16}{o.departure:%a %d}  "
                     f"{o.departure:%H:%M} {format_duration(o.duration_min):<6}"
                     f"{o.price_eur:>7.2f} {o.transfers:>2}  {o.fare.fare_class.label:<9}"
                     f"{co2:>6}{best.total:>8.2f}")
    lines.append(f"{len(winners)} different offer(s) recommended for {len(results)} profile(s).")

    table = cross_ranks(results)
    if table:
        ids = [r.profile.profile_id for r in results]
        lines.append("")
        lines.append("Rank of each profile's winner under every profile (- = removed by "
                     "that profile's hard constraints):")
        lines.append(f"{'Winner of':<11}" + "".join(f"{i:>10}" for i in ids))
        for owner, row in table.items():
            cells = "".join(f"{('-' if row[i] is None else '#' + str(row[i])):>10}" for i in ids)
            lines.append(f"{owner:<11}{cells}")
    return lines


def format_booking(booking: Booking) -> List[str]:
    return ["BOOKING CONFIRMATION"] + [f"  {line}" for line in confirmation_lines(booking)]


def format_booking_list(bookings: List[Booking]) -> List[str]:
    if not bookings:
        return ["No bookings yet."]
    header = f"{'Reference':<11}{'Status':<11}{'Passenger':<22}{'Departure':<18}{'To':<14}{'EUR':>8}"
    lines = [header, "-" * len(header)]
    for b in bookings:
        lines.append(f"{b.reference:<11}{b.status:<11}{b.passenger_name[:21]:<22}"
                     f"{b.departure.replace('T', ' '):<18}{b.destination[:13]:<14}"
                     f"{b.total_price_eur:>8.2f}")
    return lines
