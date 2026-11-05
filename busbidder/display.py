"""Text formatting for the command-line interface.

Kept separate from the CLI so that a different interface (for example a web
page) could reuse the search and ranking code without the printing.
"""

from __future__ import annotations

from typing import List

from .criteria import get_criterion
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
            cell = "n/a" if score.missing else f"{score.score:.0f}"
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

    influence = criterion_influence(result.ranked)
    if influence:
        parts = ", ".join(f"{name} {share:.0%}" for name, share in
                          sorted(influence.items(), key=lambda kv: -kv[1]))
        lines.append(f"What actually separated the offers: {parts}")
    return lines
