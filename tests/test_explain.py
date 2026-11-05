"""Tests for the generated recommendation explanation."""

import unittest
from datetime import date

from busbidder.explain import explain, strengths_and_weaknesses
from busbidder.ranking import search
from busbidder.models import Marketplace
from busbidder.request import TimeWindow, TravelRequest
from busbidder.weights import Weights
from tests.helpers import COMPANIES, DESTINATIONS, make_offer


def market_with(*offers):
    market = Marketplace(companies=dict(COMPANIES), destinations=dict(DESTINATIONS))
    for offer in offers:
        market.add_offer(offer)
    return market


REQUEST = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14),
                        preferred_departure=TimeWindow.parse("morning"))


class TestExplanation(unittest.TestCase):
    def setUp(self):
        # Flexible direct morning offer that costs more than the alternative
        self.flex = make_offer("FLEX", price=45, duration=420, change="free", refund="full",
                               fare="F", departure="2027-07-12 09:00")
        self.cheap = make_offer("CHEAP", price=30, duration=520, change="none", refund="none",
                                fare="B", departure="2027-07-12 22:00", transfers=1)
        self.result = search(market_with(self.flex, self.cheap), REQUEST,
                             Weights.parse("price=25,duration=25,departure=25,flexibility=25"))

    def test_top_offer_is_explained_with_pros_and_cons(self):
        lines = explain(self.result)
        text = " ".join(lines)
        self.assertEqual(self.result.best.offer.offer_id, "FLEX")
        self.assertIn("ranks first", lines[0])
        self.assertIn("free changes", text)
        self.assertIn("inside your preferred morning window", text)
        self.assertIn("higher price", text)  # disadvantage is acknowledged
        self.assertIn("EUR 15.00 more than the cheapest", text)

    def test_never_claims_objective_best(self):
        last = explain(self.result)[-1]
        self.assertIn("only under the selected", last)
        self.assertNotIn("objectively best", " ".join(explain(self.result)))

    def test_lead_over_runner_up_is_quantified(self):
        text = " ".join(explain(self.result))
        self.assertIn("leads #2", text)
        self.assertIn("points", text)

    def test_explaining_a_lower_ranked_offer(self):
        lines = explain(self.result, self.result.ranked[1])
        self.assertIn("ranks #2", lines[0])
        self.assertIn("The winner leads #2", " ".join(lines))

    def test_strengths_only_include_weighted_criteria(self):
        strengths, weaknesses = strengths_and_weaknesses(self.result.best, self.result)
        self.assertFalse(any("rating" in s for s in strengths + weaknesses))

    def test_no_departure_preference_is_not_an_advantage(self):
        request = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14))
        result = search(market_with(self.flex, self.cheap), request, Weights.default())
        strengths, _ = strengths_and_weaknesses(result.best, result)
        self.assertFalse(any("departs" in s for s in strengths))

    def test_missing_data_is_mentioned(self):
        offer = make_offer("NOCO2", co2=None)
        other = make_offer("WITHCO2", co2=10.0, price=50)
        weights = Weights.parse("price=40,duration=10,departure=10,flexibility=10,emissions=30")
        result = search(market_with(offer, other), REQUEST, weights)
        noco2 = next(r for r in result.ranked if r.offer.offer_id == "NOCO2")
        text = " ".join(explain(result, noco2))
        self.assertIn("Missing data", text)
        self.assertIn("does not report emissions", text)

    def test_tie_is_explained(self):
        result = search(market_with(make_offer("B-1"), make_offer("A-1")), REQUEST,
                        Weights.default())
        self.assertIn("same total", " ".join(explain(result)))

    def test_single_offer(self):
        result = search(market_with(self.flex), REQUEST, Weights.default())
        lines = explain(result)
        self.assertIn("among 1 acceptable offer", lines[0])

    def test_no_offers(self):
        result = search(market_with(), REQUEST, Weights.default())
        self.assertEqual(explain(result), [])


if __name__ == "__main__":
    unittest.main()
