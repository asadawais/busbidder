"""Weighted ranking of the offers that passed the hard constraints.

    total(offer) = sum over criteria c of  weight_c * score_c(offer)

Every score is 0-100 and the weights total 1.0, so the total is also 0-100.
Totals are added with ``math.fsum``, which is exactly rounded. The built-in
``sum`` became more precise in Python 3.12, so without fsum a total that lies
on a rounding boundary could differ between Python versions.

Ties
----
Totals are rounded to 2 decimals before sorting, so tiny floating-point
differences do not decide the order. Equal totals are broken by, in turn:
lower price, shorter duration, earlier departure and finally the offer id.
This makes every ranking fully reproducible. Offers whose total equals the
one above them are marked ``tied`` so the explanation can say so.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .constraints import FilterResult, apply_constraints
from .criteria import get_criterion
from .models import Marketplace, Offer
from .request import TravelRequest
from .weights import Weights


@dataclass(frozen=True)
class CriterionScore:
    name: str
    raw: Optional[float]
    score: float
    weight: float

    @property
    def missing(self) -> bool:
        return self.raw is None

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass
class RankedOffer:
    rank: int
    offer: Offer
    total: float
    scores: Dict[str, CriterionScore]
    tied: bool = False


class Ranker:
    def __init__(self, weights: Weights) -> None:
        self.weights = weights

    def score(self, offers: List[Offer], request: TravelRequest) -> List[RankedOffer]:
        """Score offers without sorting (rank is filled in by ``rank``)."""
        per_offer: List[Dict[str, CriterionScore]] = [{} for _ in offers]
        for name, weight in self.weights.items():
            crit = get_criterion(name)
            raws = [crit.extract(offer, request) for offer in offers]
            present = [value for value in raws if value is not None]
            to_score = crit.scale.prepare(present)
            for scores, raw in zip(per_offer, raws):
                value = crit.missing.score if raw is None else to_score(raw)
                scores[name] = CriterionScore(name, raw, round(value, 2), weight)
        return [
            RankedOffer(rank=0, offer=offer, scores=scores,
                        total=round(math.fsum(s.contribution for s in scores.values()), 2))
            for offer, scores in zip(offers, per_offer)
        ]

    def rank(self, offers: Iterable[Offer], request: TravelRequest) -> List[RankedOffer]:
        scored = self.score(list(offers), request)
        scored.sort(key=lambda r: (-r.total, r.offer.price_eur, r.offer.duration_min,
                                   r.offer.departure, r.offer.offer_id))
        for position, item in enumerate(scored, start=1):
            item.rank = position
            item.tied = position > 1 and item.total == scored[position - 2].total
        return scored


@dataclass
class SearchResult:
    request: TravelRequest
    weights: Weights
    filtered: FilterResult
    ranked: List[RankedOffer] = field(default_factory=list)

    @property
    def best(self) -> Optional[RankedOffer]:
        return self.ranked[0] if self.ranked else None


def search(market: Marketplace, request: TravelRequest, weights: Weights) -> SearchResult:
    filtered = apply_constraints(market.all_offers(), request)
    ranked = Ranker(weights).rank(filtered.accepted, request)
    return SearchResult(request, weights, filtered, ranked)


def criterion_influence(ranked: List[RankedOffer]) -> Dict[str, float]:
    """Share of the variation in total scores that each criterion causes.

    A criterion with a high weight but identical scores for every offer has
    no influence on the order. This measure (weight x standard deviation of
    the scores, as a share of the sum) shows which criteria actually drove
    the ranking, and is used to check for unintended dominance.
    """
    if len(ranked) < 2:
        return {}
    names = list(ranked[0].scores)
    spread = {
        name: ranked[0].scores[name].weight
        * statistics.pstdev(r.scores[name].score for r in ranked)
        for name in names
    }
    total = math.fsum(spread.values())
    if total == 0:
        return {name: 0.0 for name in names}
    return {name: value / total for name, value in spread.items()}
