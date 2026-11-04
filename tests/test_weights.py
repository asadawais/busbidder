"""Tests for weight validation and parsing."""

import unittest

from busbidder.exceptions import ConfigurationError
from busbidder.weights import Weights


class TestWeights(unittest.TestCase):
    def test_default_weights_follow_specification(self):
        weights = Weights.default()
        self.assertEqual(dict(weights), {"price": 0.40, "duration": 0.25,
                                         "departure": 0.15, "flexibility": 0.20})
        self.assertEqual(weights.warnings, [])

    def test_percentages_are_converted(self):
        weights = Weights({"price": 40, "duration": 25, "departure": 15, "flexibility": 20})
        self.assertAlmostEqual(weights["price"], 0.40)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_parse_from_text(self):
        weights = Weights.parse("price=30, duration=20,departure=10,flexibility=20,rating=20")
        self.assertAlmostEqual(weights["rating"], 0.20)
        self.assertEqual(len(weights), 5)

    def test_sum_must_be_one(self):
        with self.assertRaises(ConfigurationError) as ctx:
            Weights({"price": 0.5, "duration": 0.25, "departure": 0.15, "flexibility": 0.2})
        self.assertIn("1.1", str(ctx.exception))

    def test_sum_within_tolerance(self):
        Weights({"price": 0.3334, "duration": 0.3333, "departure": 0.1666,
                 "flexibility": 0.1667})

    def test_negative_weight(self):
        with self.assertRaises(ConfigurationError):
            Weights({"price": 1.2, "duration": -0.2, "departure": 0.0, "flexibility": 0.0})

    def test_unknown_criterion(self):
        with self.assertRaises(ConfigurationError):
            Weights.parse("price=40,duration=25,departure=15,flexibility=10,comfort=10")

    def test_core_criteria_required(self):
        with self.assertRaises(ConfigurationError):
            Weights.parse("price=50,duration=50")
        with self.assertRaises(ConfigurationError):
            Weights.parse("price=40,duration=40,departure=20,flexibility=0")

    def test_bad_syntax(self):
        for text in ("price:40", "price=forty", "price=40,price=60"):
            with self.assertRaises(ConfigurationError, msg=text):
                Weights.parse(text)

    def test_dominance_warning(self):
        weights = Weights.parse("price=70,duration=10,departure=10,flexibility=10")
        self.assertEqual(len(weights.warnings), 1)
        self.assertIn("price", weights.warnings[0])

    def test_exactly_half_is_not_a_warning(self):
        weights = Weights.parse("price=50,duration=20,departure=10,flexibility=20")
        self.assertEqual(weights.warnings, [])


if __name__ == "__main__":
    unittest.main()
