"""Tests for weighted ranking, tie-breaking and reproducibility."""

import unittest
from datetime import date

from busbidder import config
from busbidder.loader import load_marketplace
from busbidder.ranking import Ranker, criterion_influence, search
from busbidder.request import TimeWindow, TravelRequest
from busbidder.weights import Weights
from tests.helpers import make_offer

REQUEST = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14),
                        preferred_departure=TimeWindow.parse("morning"))


def rank(offers, weights=None, request=REQUEST):
    return Ranker(weights or Weights.default()).rank(offers, request)


class TestRanking(unittest.TestCase):
    def test_total_is_weighted_sum_of_scores(self):
        ranked = rank([make_offer("A", price=30), make_offer("B", price=40)])
        for item in ranked:
            expected = sum(s.score * s.weight for s in item.scores.values())
            self.assertAlmostEqual(item.total, expected, places=1)
            self.assertTrue(0 <= item.total <= 100)

    def test_dominating_offer_ranks_first(self):
        good = make_offer("GOOD", price=30, duration=400, change="free", refund="full")
        bad = make_offer("BAD", price=45, duration=500, change="none", refund="none",
                         departure="2027-07-12 22:00")
        ranked = rank([bad, good])
        self.assertEqual([r.offer.offer_id for r in ranked], ["GOOD", "BAD"])
        self.assertEqual([r.rank for r in ranked], [1, 2])

    def test_weights_change_the_winner(self):
        cheap_slow = make_offer("CHEAP", price=25, duration=560)
        fast_dear = make_offer("FAST", price=45, duration=400)
        by_price = rank([cheap_slow, fast_dear],
                        Weights.parse("price=70,duration=10,departure=10,flexibility=10"))
        by_time = rank([cheap_slow, fast_dear],
                       Weights.parse("price=10,duration=70,departure=10,flexibility=10"))
        self.assertEqual(by_price[0].offer.offer_id, "CHEAP")
        self.assertEqual(by_time[0].offer.offer_id, "FAST")

    def test_tie_broken_by_price_then_id(self):
        # Identical offers apart from id: equal totals, broken by offer id
        a = make_offer("B-OFFER")
        b = make_offer("A-OFFER")
        ranked = rank([a, b])
        self.assertEqual(ranked[0].total, ranked[1].total)
        self.assertEqual(ranked[0].offer.offer_id, "A-OFFER")
        self.assertTrue(ranked[1].tied)
        self.assertFalse(ranked[0].tied)

    def test_input_order_does_not_matter(self):
        offers = [make_offer(str(i), price=30 + i, duration=400 + 7 * i) for i in range(6)]
        first = [r.offer.offer_id for r in rank(offers)]
        second = [r.offer.offer_id for r in rank(list(reversed(offers)))]
        self.assertEqual(first, second)

    def test_missing_rating_gets_neutral_score(self):
        weights = Weights.parse("price=30,duration=20,departure=15,flexibility=15,rating=20")
        ranked = rank([make_offer("A", rating=None), make_offer("B", rating=5.0)], weights)
        scores = {r.offer.offer_id: r.scores["rating"] for r in ranked}
        self.assertTrue(scores["A"].missing)
        self.assertEqual(scores["A"].score, 50)
        self.assertEqual(scores["B"].score, 100)

    def test_missing_emissions_get_worst_score(self):
        weights = Weights.parse("price=30,duration=20,departure=15,flexibility=15,emissions=20")
        ranked = rank([make_offer("A", co2=None), make_offer("B", co2=10.0)], weights)
        scores = {r.offer.offer_id: r.scores["emissions"].score for r in ranked}
        self.assertEqual(scores["A"], 0)
        self.assertEqual(scores["B"], 100)

    def test_empty_list(self):
        self.assertEqual(rank([]), [])

    def test_single_offer(self):
        ranked = rank([make_offer()])
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[0].scores["price"].score, 100)

    def test_influence_shares_sum_to_one(self):
        offers = [make_offer(str(i), price=30 + 3 * i, duration=400 + 20 * i) for i in range(5)]
        influence = criterion_influence(rank(offers))
        self.assertAlmostEqual(sum(influence.values()), 1.0)
        # Every offer departs at the same time, so departure cannot separate them
        self.assertEqual(influence["departure"], 0)


class TestSearchOnDataset(unittest.TestCase):
    """End-to-end check against the generated dataset (example request in the spec)."""

    @classmethod
    def setUpClass(cls):
        cls.market = load_marketplace(config.DATA_DIR, quarantine_dir=None).marketplace

    def example_request(self):
        return TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14), max_price_eur=50,
                             preferred_departure=TimeWindow.parse("morning"),
                             max_transfers=1, require_changeable=True)

    def test_every_ranked_offer_satisfies_the_hard_constraints(self):
        result = search(self.market, self.example_request(), Weights.default())
        self.assertGreater(len(result.ranked), 0)
        for item in result.ranked:
            o = item.offer
            self.assertEqual(o.destination.code, "MAL")
            self.assertTrue(date(2027, 7, 12) <= o.travel_date <= date(2027, 7, 14))
            self.assertLessEqual(o.price_eur, 50)
            self.assertLessEqual(o.transfers, 1)
            self.assertTrue(o.fare.is_changeable)
            self.assertGreater(o.seats_available, 0)

    def test_ranking_is_reproducible(self):
        first = search(self.market, self.example_request(), Weights.default())
        second = search(self.market, self.example_request(), Weights.default())
        self.assertEqual([(r.offer.offer_id, r.total) for r in first.ranked],
                         [(r.offer.offer_id, r.total) for r in second.ranked])

    def test_totals_are_sorted_descending(self):
        result = search(self.market, self.example_request(), Weights.default())
        totals = [r.total for r in result.ranked]
        self.assertEqual(totals, sorted(totals, reverse=True))


if __name__ == "__main__":
    unittest.main()
