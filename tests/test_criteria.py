"""Tests for criterion normalisation, including extreme and missing values."""

import unittest
from datetime import date

from busbidder.criteria import (
    CORE_CRITERIA,
    AbsoluteScale,
    MissingPolicy,
    RelativeScale,
    flexibility_points,
    get_criterion,
    registered_criteria,
)
from busbidder.request import TimeWindow, TravelRequest
from tests.helpers import make_offer

REQUEST = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14),
                        preferred_departure=TimeWindow.parse("morning"))


class TestRelativeScale(unittest.TestCase):
    def test_lower_is_better_min_max(self):
        score = RelativeScale(lower_is_better=True, min_range=1).prepare([10, 20, 30])
        self.assertEqual(score(10), 100)
        self.assertEqual(score(20), 50)
        self.assertEqual(score(30), 0)

    def test_higher_is_better(self):
        score = RelativeScale(lower_is_better=False, min_range=1).prepare([1, 3])
        self.assertEqual(score(3), 100)
        self.assertEqual(score(1), 0)

    def test_all_equal_values_score_100(self):
        score = RelativeScale(lower_is_better=True, min_range=5).prepare([40, 40, 40])
        self.assertEqual(score(40), 100)

    def test_minimum_range_prevents_exaggerating_small_differences(self):
        # Prices 30.00 and 31.00: plain min-max would give 100 and 0
        score = RelativeScale(lower_is_better=True, min_range=5).prepare([30.0, 31.0])
        self.assertEqual(score(30.0), 100)
        self.assertAlmostEqual(score(31.0), 80)

    def test_outlier_does_not_squash_the_others(self):
        values = [30, 32, 34, 36, 38, 200]
        score = RelativeScale(lower_is_better=True, min_range=5).prepare(values)
        self.assertEqual(score(30), 100)
        self.assertEqual(score(200), 0)
        # Without the fence, 38 would score about 95 (scale up to 200)
        self.assertLess(score(38), 60)

    def test_single_value(self):
        score = RelativeScale(lower_is_better=True, min_range=5).prepare([42])
        self.assertEqual(score(42), 100)

    def test_no_values(self):
        self.assertEqual(RelativeScale(True, 5).prepare([])(10), 0)

    def test_scores_are_clamped(self):
        score = AbsoluteScale(lambda x: x * 10, "").prepare([])
        self.assertEqual(score(50), 100)
        self.assertEqual(score(-5), 0)


class TestCriteria(unittest.TestCase):
    def raw(self, name, offer, request=REQUEST):
        return get_criterion(name).extract(offer, request)

    def test_core_criteria_registered(self):
        names = [c.name for c in registered_criteria()]
        for name in CORE_CRITERIA:
            self.assertIn(name, names)
        self.assertTrue(all(get_criterion(n).core for n in CORE_CRITERIA))
        self.assertFalse(get_criterion("rating").core)

    def test_departure_inside_and_outside_window(self):
        scale = get_criterion("departure").scale.prepare([])
        inside = self.raw("departure", make_offer(departure="2027-07-12 09:00"))
        two_hours = self.raw("departure", make_offer(departure="2027-07-12 14:00"))
        far = self.raw("departure", make_offer(departure="2027-07-12 22:00"))
        self.assertEqual(scale(inside), 100)
        self.assertEqual(scale(two_hours), 50)
        self.assertEqual(scale(far), 0)

    def test_no_departure_preference_is_neutral(self):
        no_pref = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 12))
        self.assertEqual(self.raw("departure", make_offer(), no_pref), 0)

    def test_flexibility_points(self):
        self.assertEqual(flexibility_points(make_offer(change="none", refund="none")), 0)
        self.assertEqual(flexibility_points(make_offer(change="fee", refund="none")), 30)
        self.assertEqual(flexibility_points(make_offer(change="fee", refund="partial")), 50)
        self.assertEqual(flexibility_points(make_offer(change="free", refund="full")), 100)

    def test_transfer_points(self):
        scale = get_criterion("transfers").scale.prepare([])
        self.assertEqual([scale(n) for n in (0, 1, 2, 3)], [100, 60, 25, 0])

    def test_rating_scale(self):
        scale = get_criterion("rating").scale.prepare([])
        self.assertEqual(scale(1.0), 0)
        self.assertEqual(scale(5.0), 100)
        self.assertEqual(scale(3.0), 50)

    def test_missing_value_policies(self):
        self.assertIsNone(self.raw("rating", make_offer(rating=None)))
        self.assertIs(get_criterion("rating").missing, MissingPolicy.NEUTRAL)
        self.assertIs(get_criterion("emissions").missing, MissingPolicy.WORST)
        self.assertEqual(MissingPolicy.NEUTRAL.score, 50)
        self.assertEqual(MissingPolicy.WORST.score, 0)


if __name__ == "__main__":
    unittest.main()
