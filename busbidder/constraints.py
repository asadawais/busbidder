"""Hard constraints and the filter that applies them.

Each constraint is a small function with the @hard_constraint decorator. The
decorator adds it to a registry together with a description and a rule for
when it applies. The filter just loops over the registry, so adding a new
constraint only means writing one new function. The registry order is also
the order used when counting removed offers in the summary.

A constraint returns None if the offer is OK, otherwise a short reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .models import Offer
from .request import TravelRequest

CheckFn = Callable[[Offer, TravelRequest], Optional[str]]
AppliesFn = Callable[[TravelRequest], bool]


@dataclass(frozen=True)
class HardConstraint:
    name: str
    description: str
    check: CheckFn
    applies: AppliesFn


_REGISTRY: Dict[str, HardConstraint] = {}


def hard_constraint(name: str, description: str,
                    applies: AppliesFn = lambda request: True) -> Callable[[CheckFn], CheckFn]:
    """Register a function as a hard constraint."""

    def decorator(func: CheckFn) -> CheckFn:
        if name in _REGISTRY:
            raise ValueError(f"Constraint {name!r} is already registered")
        _REGISTRY[name] = HardConstraint(name, description, func, applies)
        return func

    return decorator


def registered_constraints() -> List[HardConstraint]:
    return list(_REGISTRY.values())


def active_constraints(request: TravelRequest) -> List[HardConstraint]:
    return [c for c in _REGISTRY.values() if c.applies(request)]


# Constraints


@hard_constraint("destination", "Offer goes to the requested destination")
def _destination(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.destination.code != request.destination:
        return f"goes to {offer.destination.name}"
    return None


@hard_constraint("travel_window", "Departure date is inside the travel window")
def _travel_window(offer: Offer, request: TravelRequest) -> Optional[str]:
    if not request.earliest_date <= offer.travel_date <= request.latest_date:
        return f"departs {offer.travel_date:%d %b}, outside the travel window"
    return None


@hard_constraint("seats", "Enough seats for all passengers")
def _seats(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.seats_available < request.passengers:
        if offer.seats_available == 0:
            return "sold out"
        return f"only {offer.seats_available} seat(s) left"
    return None


@hard_constraint("budget", "Ticket price within the maximum price",
                 applies=lambda r: r.max_price_eur is not None)
def _budget(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.price_eur > request.max_price_eur:
        return f"costs EUR {offer.price_eur:.2f}, above EUR {request.max_price_eur:.2f}"
    return None


@hard_constraint("max_transfers", "No more transfers than allowed",
                 applies=lambda r: r.max_transfers is not None)
def _max_transfers(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.transfers > request.max_transfers:
        return f"{offer.transfers} transfers, maximum is {request.max_transfers}"
    return None


@hard_constraint("changeable", "Ticket can be changed",
                 applies=lambda r: r.require_changeable)
def _changeable(offer: Offer, request: TravelRequest) -> Optional[str]:
    if not offer.fare.is_changeable:
        return "ticket cannot be changed"
    return None


@hard_constraint("refundable", "Ticket can be refunded",
                 applies=lambda r: r.require_refundable)
def _refundable(offer: Offer, request: TravelRequest) -> Optional[str]:
    if not offer.fare.is_refundable:
        return "ticket is non-refundable"
    return None


@hard_constraint("luggage", "Enough checked bags included",
                 applies=lambda r: r.min_luggage_bags is not None)
def _luggage(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.fare.luggage_bags < request.min_luggage_bags:
        return f"{offer.fare.luggage_bags} checked bag(s) included"
    return None


@hard_constraint("max_duration", "Journey not longer than the maximum",
                 applies=lambda r: r.max_duration_min is not None)
def _max_duration(offer: Offer, request: TravelRequest) -> Optional[str]:
    if offer.duration_min > request.max_duration_min:
        return f"takes {offer.duration_min} min, maximum is {request.max_duration_min}"
    return None


# Filtering


@dataclass
class FilterResult:
    """The accepted offers and why every other offer was removed.

    in_scope is the number of offers to the right destination inside the travel
    window, so the offers the traveller could have picked. The summary reports
    removals out of this number.
    """

    accepted: List[Offer] = field(default_factory=list)
    reasons: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    removed_by: Dict[str, int] = field(default_factory=dict)
    in_scope: int = 0

    def summary_lines(self) -> List[str]:
        lines = [f"Offers on this route in the travel window: {self.in_scope}"]
        for name, count in self.removed_by.items():
            if name in ("destination", "travel_window") or count == 0:
                continue
            lines.append(f"  removed by {name.replace('_', ' ')}: {count}")
        lines.append(f"Offers that satisfy every hard constraint: {len(self.accepted)}")
        return lines


SCOPE_CONSTRAINTS = ("destination", "travel_window")


def apply_constraints(offers: Iterable[Offer], request: TravelRequest) -> FilterResult:
    """Split offers into accepted and removed.

    All active constraints are checked so every reason is kept, but each removed
    offer is only counted once, under the first constraint it fails.
    """
    constraints = active_constraints(request)
    result = FilterResult(removed_by={c.name: 0 for c in constraints})
    for offer in offers:
        failures = []
        for constraint in constraints:
            reason = constraint.check(offer, request)
            if reason is not None:
                failures.append((constraint.name, reason))
        if not any(name in SCOPE_CONSTRAINTS for name, _ in failures):
            result.in_scope += 1
        if failures:
            result.reasons[offer.offer_id] = failures
            result.removed_by[failures[0][0]] += 1
        else:
            result.accepted.append(offer)
    return result
