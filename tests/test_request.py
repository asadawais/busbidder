"""Tests for travel requests and departure windows."""

import unittest
from datetime import date, time

from busbidder.exceptions import InvalidRequestError
from busbidder.request import TimeWindow, TravelRequest


def request(**overrides):
    values = dict(destination="MAL", earliest_date=date(2027, 7, 12),
                  latest_date=date(2027, 7, 14))
    values.update(overrides)
    return TravelRequest(**values)


class TestTimeWindow(unittest.TestCase):
    def test_preset(self):
        window = TimeWindow.parse("Morning")
        self.assertEqual((window.start, window.end), (time(6), time(12)))

    def test_custom_window(self):
        window = TimeWindow.parse("07:30 - 09:00")
        self.assertEqual((window.start, window.end), (time(7, 30), time(9)))

    def test_invalid_window(self):
        for text in ("brunch", "7-9", "10:00-10:00", "25:00-26:00"):
            with self.assertRaises(InvalidRequestError, msg=text):
                TimeWindow.parse(text)

    def test_inside_window_is_zero_distance(self):
        window = TimeWindow.parse("morning")
        for t in (time(6), time(9), time(12)):  # edges are inside
            self.assertEqual(window.minutes_outside(t), 0)

    def test_distance_outside(self):
        window = TimeWindow.parse("morning")
        self.assertEqual(window.minutes_outside(time(13, 30)), 90)
        self.assertEqual(window.minutes_outside(time(5, 0)), 60)

    def test_distance_wraps_around_midnight(self):
        window = TimeWindow.parse("morning")
        # 23:30 is 6.5 h before 06:00 (around the clock), not 11.5 h after 12:00
        self.assertEqual(window.minutes_outside(time(23, 30)), 390)

    def test_window_across_midnight(self):
        window = TimeWindow.parse("night")  # 21:00-02:00
        self.assertTrue(window.contains(time(23, 30)))
        self.assertTrue(window.contains(time(1, 0)))
        self.assertFalse(window.contains(time(12, 0)))
        self.assertEqual(window.minutes_outside(time(20, 0)), 60)


class TestTravelRequest(unittest.TestCase):
    def test_valid_request(self):
        r = request(max_price_eur=50, max_transfers=1, require_changeable=True)
        self.assertEqual(r.passengers, 1)
        self.assertIn("Ticket must be changeable", r.hard_constraint_lines())

    def test_single_day_window_is_allowed(self):
        r = request(latest_date=date(2027, 7, 12))
        self.assertEqual(r.earliest_date, r.latest_date)

    def test_window_ending_before_start(self):
        with self.assertRaises(InvalidRequestError):
            request(latest_date=date(2027, 7, 11))

    def test_invalid_values(self):
        bad = [dict(max_price_eur=0), dict(max_price_eur=-5), dict(max_price_eur=float("nan")),
               dict(max_price_eur=float("inf")), dict(max_duration_min=float("nan")),
               dict(max_transfers=-1),
               dict(max_transfers=4), dict(passengers=0), dict(passengers=10),
               dict(max_duration_min=0), dict(min_luggage_bags=5), dict(destination="mal")]
        for overrides in bad:
            with self.assertRaises(InvalidRequestError, msg=overrides):
                request(**overrides)

    def test_preferences_are_listed_separately(self):
        r = request(preferred_departure=TimeWindow.parse("morning"))
        self.assertNotIn("morning", " ".join(r.hard_constraint_lines()))
        self.assertIn("morning", r.preference_lines()[0])


if __name__ == "__main__":
    unittest.main()
