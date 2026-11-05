"""Generate a plain-language explanation for a ranked offer.

The explanation is built only from the data: the offer's raw values, its
criterion scores, the weights and the other acceptable offers. It names the
offer's strongest and weakest criteria, says what separated it from the
runner-up, flags missing data and ends with a reminder that the result
depends on the chosen constraints, weights and scoring assumptions.

Classification
--------------
* strength: criterion score >= 70
* weakness: criterion score <= 40
Within each group the criteria are ordered by weight x score distance from
neutral, so the points that mattered most for this traveller come first.
Only criteria with a weight are considered, so the text always matches
what the traveller said was important.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .criteria import get_criterion
from .models import ChangePolicy, Offer, RefundPolicy
from .ranking import RankedOffer, SearchResult

STRENGTH_THRESHOLD = 70.0
WEAKNESS_THRESHOLD = 40.0
MAX_POINTS = 3


def _hm(minutes: float) -> str:
    hours, mins = divmod(int(round(minutes)), 60)
    if hours and mins:
        return f"{hours} h {mins:02d} min"
    return f"{hours} h" if hours else f"{mins} min"


class _Context:
    """Facts about the whole set of acceptable offers, used for comparisons."""

    def __init__(self, result: SearchResult) -> None:
        offers = [r.offer for r in result.ranked]
        self.request = result.request
        self.min_price = min(o.price_eur for o in offers)
        self.max_price = max(o.price_eur for o in offers)
        self.min_duration = min(o.duration_min for o in offers)
        co2 = [o.co2_kg for o in offers if o.co2_kg is not None]
        self.min_co2 = min(co2) if co2 else None
        self.count = len(offers)


def _price(o: Offer, ctx: _Context, good: bool) -> str:
    if o.price_eur == ctx.min_price:
        return f"the cheapest acceptable offer (EUR {o.price_eur:.2f})"
    diff = o.price_eur - ctx.min_price
    if good:
        return f"low price (EUR {o.price_eur:.2f}, only EUR {diff:.2f} above the cheapest)"
    return f"higher price (EUR {o.price_eur:.2f}, EUR {diff:.2f} more than the cheapest)"


def _duration(o: Offer, ctx: _Context, good: bool) -> str:
    if o.duration_min == ctx.min_duration:
        return f"the fastest acceptable journey ({_hm(o.duration_min)})"
    diff = o.duration_min - ctx.min_duration
    if good:
        return f"short journey ({_hm(o.duration_min)}, {_hm(diff)} longer than the fastest)"
    return f"long journey ({_hm(o.duration_min)}, {_hm(diff)} longer than the fastest)"


def _departure(o: Offer, ctx: _Context, good: bool) -> str:
    window = ctx.request.preferred_departure
    when = f"{o.departure:%H:%M}"
    if window is None:
        return f"departs at {when}"
    outside = window.minutes_outside(o.departure.time())
    if outside == 0:
        return f"departs at {when}, inside your preferred {window.label} window"
    return f"departs at {when}, {_hm(outside)} outside your preferred {window.label} window"


def _flexibility(o: Offer, ctx: _Context, good: bool) -> str:
    f = o.fare
    change = {ChangePolicy.NONE: "no changes allowed", ChangePolicy.FEE:
              f"changes cost EUR {f.change_fee_eur:.2f}", ChangePolicy.FREE: "free changes"}
    refund = {RefundPolicy.NONE: "non-refundable", RefundPolicy.PARTIAL: "partial refund",
              RefundPolicy.FULL: "full refund"}
    return f"{f.fare_class.label} fare: {change[f.change_policy]}, {refund[f.refund_policy]}"


def _transfers(o: Offer, ctx: _Context, good: bool) -> str:
    if o.transfers == 0:
        return "direct route with no transfers"
    return f"{o.transfers} transfer" + ("s" if o.transfers > 1 else "")


def _rating(o: Offer, ctx: _Context, good: bool) -> str:
    if o.rating is None:
        return "no customer rating available"
    return f"customer rating {o.rating:.1f} of 5"


def _emissions(o: Offer, ctx: _Context, good: bool) -> str:
    if o.co2_kg is None:
        return f"{o.company.name} does not report emissions"
    if ctx.min_co2 is not None and o.co2_kg == ctx.min_co2:
        return f"lowest emissions ({o.co2_kg:.1f} kg CO2)"
    return f"{o.co2_kg:.1f} kg CO2 per passenger"


PHRASES: Dict[str, Callable[[Offer, _Context, bool], str]] = {
    "price": _price,
    "duration": _duration,
    "departure": _departure,
    "flexibility": _flexibility,
    "transfers": _transfers,
    "rating": _rating,
    "emissions": _emissions,
}


def _phrase(name: str, offer: Offer, ctx: _Context, good: bool) -> str:
    builder = PHRASES.get(name)
    if builder is None:  # a newly registered criterion without a phrase
        return f"{get_criterion(name).label.lower()} score"
    return builder(offer, ctx, good)


def strengths_and_weaknesses(item: RankedOffer, result: SearchResult):
    ctx = _Context(result)
    scored = [s for s in item.scores.values() if s.weight > 0]
    if result.request.preferred_departure is None:
        # Every departure scores 100 without a preference, so it is no advantage
        scored = [s for s in scored if s.name != "departure"]
    strong = sorted((s for s in scored if s.score >= STRENGTH_THRESHOLD),
                    key=lambda s: -s.weight * (s.score - 50))
    weak = sorted((s for s in scored if s.score <= WEAKNESS_THRESHOLD),
                  key=lambda s: -s.weight * (50 - s.score))
    return ([_phrase(s.name, item.offer, ctx, True) for s in strong[:MAX_POINTS]],
            [_phrase(s.name, item.offer, ctx, False) for s in weak[:MAX_POINTS]])


def _margin_line(item: RankedOffer, other: RankedOffer) -> str:
    """Which criteria gave ``item`` its lead over ``other`` (weighted points)."""
    diffs = {name: item.scores[name].contribution - other.scores[name].contribution
             for name in item.scores}
    gains = sorted(((n, d) for n, d in diffs.items() if d > 0.05), key=lambda kv: -kv[1])
    losses = sorted(((n, d) for n, d in diffs.items() if d < -0.05), key=lambda kv: kv[1])

    def label(name: str) -> str:
        return get_criterion(name).label.lower()

    margin = item.total - other.total
    text = f"It leads #{other.rank} ({other.offer.short_label()}) by {margin:.2f} points"
    if gains:
        text += ", gaining on " + ", ".join(f"{label(n)} (+{d:.1f})" for n, d in gains[:2])
    if losses:
        text += " while losing on " + ", ".join(f"{label(n)} ({d:.1f})" for n, d in losses[:2])
    return text + "."


def explain(result: SearchResult, item: Optional[RankedOffer] = None) -> List[str]:
    """Return explanation lines for ``item`` (default: the top-ranked offer)."""
    if not result.ranked:
        return []
    item = item or result.ranked[0]
    offer = item.offer
    position = "ranks first" if item.rank == 1 else f"ranks #{item.rank}"
    lines = [
        f"{offer.short_label()} {position} with {item.total:.2f} of 100 points "
        f"among {len(result.ranked)} acceptable offer(s)."
    ]
    strengths, weaknesses = strengths_and_weaknesses(item, result)
    if strengths:
        lines.append("Advantages: " + "; ".join(strengths) + ".")
    if weaknesses:
        lines.append("Disadvantages: " + "; ".join(weaknesses) + ".")
    if not weaknesses:
        lines.append("Disadvantages: none of its weighted criteria scores below "
                     f"{WEAKNESS_THRESHOLD:.0f}.")

    if item.rank == 1 and len(result.ranked) > 1:
        runner_up = result.ranked[1]
        if item.total == runner_up.total:
            lines.append(f"It has the same total as #2 ({runner_up.offer.short_label()}) "
                         "and is placed first by the tie-break (lower price, then shorter "
                         "journey, then earlier departure).")
        else:
            lines.append(_margin_line(item, runner_up))
    elif item.rank > 1:
        lines.append(_margin_line(result.ranked[0], item).replace("It leads", "The winner leads"))

    missing = [get_criterion(s.name).label.lower() for s in item.scores.values()
               if s.missing and s.weight > 0]
    if missing:
        policies = ", ".join(f"{get_criterion(s.name).label.lower()} scored "
                             f"{s.score:.0f}" for s in item.scores.values()
                             if s.missing and s.weight > 0)
        lines.append(f"Missing data: {', '.join(missing)} not available ({policies}).")

    lines.append("This is the best match only under the selected hard constraints, weights "
                 "and scoring assumptions, not an objective judgement of the offers.")
    return lines
