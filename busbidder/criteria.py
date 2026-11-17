"""Scoring criteria and how each one is turned into a 0-100 score.

Euros, minutes and categories can't be added together, so every criterion
converts its raw value to a score from 0 (worst) to 100 (best).

There are two kinds of scales.

RelativeScale compares the offers with each other (price, duration,
emissions). The best value among the accepted offers gets 100. It has two
safeguards:
  - Outlier fences: with at least four values the end points are clipped to
    the Tukey fences (Q1 - 1.5*IQR, Q3 + 1.5*IQR). One extreme offer then
    gets 0 instead of pushing all the others up to the top.
  - Minimum range: if all values are close (e.g. prices within EUR 2), plain
    min-max would still spread them from 0 to 100. The scale is widened to at
    least min_range (EUR 5, 30 min, 2 kg CO2) so small differences only give
    small score gaps.

AbsoluteScale uses a fixed formula that doesn't depend on the other offers
(departure, flexibility, transfers, rating). These have a natural best and
worst value, which makes them easier to explain.

Missing values:
  - missing rating gives a neutral 50 (no rating doesn't mean the company is bad)
  - missing emissions gives 0 (worst). Someone who weights emissions wants low
    emissions that are actually reported, and unknown emissions don't show that.
Both choices can be unfair, which I discuss in the report.

Criteria are registered with the @criterion decorator, the same way as the
hard constraints. The ranking code only uses the registry, so a new criterion
is one decorated function and can be used in the weights right away.
"""

from __future__ import annotations

import dataclasses
import statistics
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from . import config
from .models import ChangePolicy, Offer, RefundPolicy
from .request import TravelRequest

ScoreFn = Callable[[float], float]
ExtractFn = Callable[[Offer, TravelRequest], Optional[float]]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class MissingPolicy(str, Enum):
    NEUTRAL = "neutral"  # score 50
    WORST = "worst"  # score 0

    @property
    def score(self) -> float:
        return 50.0 if self is MissingPolicy.NEUTRAL else 0.0


@dataclass(frozen=True)
class RelativeScale:
    lower_is_better: bool
    min_range: float
    use_fences: bool = True  # can be switched off for the normalisation experiment

    def bounds(self, values: Sequence[float]) -> tuple:
        lo, hi = min(values), max(values)
        if self.use_fences and len(values) >= 4:
            q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
            iqr = q3 - q1
            lo = max(lo, q1 - 1.5 * iqr)
            hi = min(hi, q3 + 1.5 * iqr)
        if hi - lo < self.min_range:
            # widen on the side away from the best value so the best still gets 100
            if self.lower_is_better:
                hi = lo + self.min_range
            else:
                lo = hi - self.min_range
        return lo, hi

    def prepare(self, values: Sequence[float]) -> ScoreFn:
        if not values:
            return lambda x: 0.0
        lo, hi = self.bounds(values)
        span = hi - lo
        if span <= 0:  # all values are the same and there is no min range
            return lambda x: 100.0
        if self.lower_is_better:
            return lambda x: clamp((hi - x) / span * 100)
        return lambda x: clamp((x - lo) / span * 100)

    def describe(self) -> str:
        direction = "lower is better" if self.lower_is_better else "higher is better"
        fences = "with outlier fences" if self.use_fences else "without outlier fences"
        return f"relative min-max {fences}, {direction}, minimum range {self.min_range:g}"


@dataclass(frozen=True)
class AbsoluteScale:
    func: ScoreFn
    text: str

    def prepare(self, values: Sequence[float]) -> ScoreFn:
        return lambda x: clamp(self.func(x))

    def describe(self) -> str:
        return self.text


@dataclass(frozen=True)
class Criterion:
    name: str
    label: str
    unit: str
    description: str
    extract: ExtractFn
    scale: object  # RelativeScale or AbsoluteScale
    core: bool
    missing: MissingPolicy


_REGISTRY: Dict[str, Criterion] = {}

# The four criteria that have to be in every ranking (from the spec).
CORE_CRITERIA = ("price", "duration", "departure", "flexibility")


def criterion(name: str, *, label: str, unit: str, description: str, scale: object,
              missing: MissingPolicy = MissingPolicy.NEUTRAL) -> Callable[[ExtractFn], ExtractFn]:
    """Register a function that extracts a criterion's raw value from an offer."""

    def decorator(func: ExtractFn) -> ExtractFn:
        if name in _REGISTRY:
            raise ValueError(f"Criterion {name!r} is already registered")
        _REGISTRY[name] = Criterion(name, label, unit, description, func, scale,
                                    name in CORE_CRITERIA, missing)
        return func

    return decorator


def get_criterion(name: str) -> Criterion:
    return _REGISTRY[name]


@contextmanager
def overridden(name: str, **changes: Any) -> Iterator[Criterion]:
    """Temporarily change a registered criterion, e.g. its scale or missing policy.

    Used in the experiments to see how much a design choice matters (for example
    missing emissions as worst vs neutral). The original is always put back,
    even if something fails.
    """
    original = _REGISTRY[name]
    _REGISTRY[name] = dataclasses.replace(original, **changes)
    try:
        yield _REGISTRY[name]
    finally:
        _REGISTRY[name] = original


def registered_criteria() -> List[Criterion]:
    return list(_REGISTRY.values())


def criterion_names() -> List[str]:
    return list(_REGISTRY)


# Points for flexibility and transfers

CHANGE_POINTS = {ChangePolicy.NONE: 0, ChangePolicy.FEE: 50, ChangePolicy.FREE: 100}
REFUND_POINTS = {RefundPolicy.NONE: 0, RefundPolicy.PARTIAL: 50, RefundPolicy.FULL: 100}
CHANGE_SHARE = 0.6  # being able to change matters more than a refund
TRANSFER_POINTS = {0: 100.0, 1: 60.0, 2: 25.0, 3: 0.0}
DEPARTURE_ZERO_AFTER_MIN = 240  # 4 hours outside the window scores 0


def flexibility_points(offer: Offer) -> float:
    change = CHANGE_POINTS[offer.fare.change_policy]
    refund = REFUND_POINTS[offer.fare.refund_policy]
    return CHANGE_SHARE * change + (1 - CHANGE_SHARE) * refund


# Criteria


@criterion("price", label="Price", unit="EUR",
           description="Ticket price, cheaper is better",
           scale=RelativeScale(lower_is_better=True, min_range=5.0))
def _price(offer: Offer, request: TravelRequest) -> Optional[float]:
    return offer.price_eur


@criterion("duration", label="Duration", unit="min",
           description="Total journey time including transfers, shorter is better",
           scale=RelativeScale(lower_is_better=True, min_range=30.0))
def _duration(offer: Offer, request: TravelRequest) -> Optional[float]:
    return float(offer.duration_min)


@criterion("departure", label="Departure", unit="min outside window",
           description="Minutes between departure and the preferred window",
           scale=AbsoluteScale(lambda m: 100 - m / DEPARTURE_ZERO_AFTER_MIN * 100,
                               "100 inside the window, falling linearly to 0 at 4 h outside"))
def _departure(offer: Offer, request: TravelRequest) -> Optional[float]:
    if request.preferred_departure is None:
        return 0.0  # no preference: every departure is equally convenient
    return float(request.preferred_departure.minutes_outside(offer.departure.time()))


@criterion("flexibility", label="Flexibility", unit="points",
           description="Change and refund conditions",
           scale=AbsoluteScale(lambda p: p, "60% change policy (none 0, fee 50, free 100) "
                                            "+ 40% refund policy (none 0, partial 50, full 100)"))
def _flexibility(offer: Offer, request: TravelRequest) -> Optional[float]:
    return flexibility_points(offer)


@criterion("transfers", label="Transfers", unit="count",
           description="Number of changes of bus, fewer is better",
           scale=AbsoluteScale(lambda n: TRANSFER_POINTS.get(int(n), 0.0),
                               "0 transfers 100, 1 -> 60, 2 -> 25, 3 -> 0"))
def _transfers(offer: Offer, request: TravelRequest) -> Optional[float]:
    return float(offer.transfers)


@criterion("rating", label="Rating", unit="of 5",
           description="Customer rating, higher is better",
           scale=AbsoluteScale(
               lambda r: (r - config.RATING_MIN) / (config.RATING_MAX - config.RATING_MIN) * 100,
               "linear from 1.0 (0) to 5.0 (100)"),
           missing=MissingPolicy.NEUTRAL)
def _rating(offer: Offer, request: TravelRequest) -> Optional[float]:
    return offer.rating


@criterion("emissions", label="CO2", unit="kg",
           description="Estimated CO2 per passenger, lower is better",
           scale=RelativeScale(lower_is_better=True, min_range=2.0),
           missing=MissingPolicy.WORST)
def _emissions(offer: Offer, request: TravelRequest) -> Optional[float]:
    return offer.co2_kg
