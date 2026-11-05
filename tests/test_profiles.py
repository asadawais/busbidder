"""Tests for traveller profiles and profile comparison."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from busbidder import config
from busbidder.exceptions import ConfigurationError, DataFileError
from busbidder.loader import load_marketplace
from busbidder.profiles import (
    compare_profiles,
    cross_ranks,
    load_profiles,
    select_profiles,
)
from busbidder.request import TravelRequest

BASE = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14))


def write_profiles(folder, profiles):
    path = Path(folder) / "profiles.json"
    path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
    return path


GOOD = {"id": "p", "name": "P", "weights": {"price": 40, "duration": 25, "departure": 15,
                                            "flexibility": 20}}


class TestLoadProfiles(unittest.TestCase):
    def test_shipped_profiles_load(self):
        profiles = load_profiles(config.DATA_DIR / "profiles.json")
        self.assertGreaterEqual(len(profiles), 3)
        self.assertIn("budget", profiles)
        self.assertIn("business", profiles)
        self.assertIn("flexible", profiles)

    def test_invalid_weights_name_the_profile(self):
        bad = dict(GOOD, weights={"price": 90, "duration": 25})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigurationError) as ctx:
                load_profiles(write_profiles(tmp, [bad]))
            self.assertIn("'p'", str(ctx.exception))

    def test_unknown_constraint(self):
        bad = dict(GOOD, constraints={"max_price": 30})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigurationError):
                load_profiles(write_profiles(tmp, [bad]))

    def test_duplicate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigurationError):
                load_profiles(write_profiles(tmp, [GOOD, GOOD]))

    def test_missing_file(self):
        with self.assertRaises(DataFileError):
            load_profiles(Path("does/not/exist.json"))

    def test_select_unknown_profile(self):
        profiles = load_profiles(config.DATA_DIR / "profiles.json")
        with self.assertRaises(ConfigurationError):
            select_profiles(profiles, ["budget", "pirate"])


class TestApplyProfile(unittest.TestCase):
    def setUp(self):
        self.profiles = load_profiles(config.DATA_DIR / "profiles.json")

    def test_profile_adds_constraints_and_departure(self):
        request = self.profiles["business"].apply(BASE)
        self.assertTrue(request.require_changeable)
        self.assertEqual(request.max_transfers, 0)
        self.assertIsNotNone(request.preferred_departure)
        self.assertEqual(request.destination, "MAL")

    def test_stricter_value_wins(self):
        base = TravelRequest("MAL", date(2027, 7, 12), date(2027, 7, 14), max_transfers=1,
                             min_luggage_bags=2)
        business = self.profiles["business"].apply(base)  # max_transfers 0
        self.assertEqual(business.max_transfers, 0)
        flexible = self.profiles["flexible"].apply(base)  # min_luggage_bags 1
        self.assertEqual(flexible.min_luggage_bags, 2)

    def test_profile_does_not_change_base(self):
        self.profiles["business"].apply(BASE)
        self.assertFalse(BASE.require_changeable)


class TestCompareOnDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_marketplace(config.DATA_DIR, quarantine_dir=None).marketplace
        cls.profiles = load_profiles(config.DATA_DIR / "profiles.json")

    def test_profiles_get_different_recommendations(self):
        results = compare_profiles(self.market, BASE, list(self.profiles.values()))
        winners = {r.result.best.offer.offer_id for r in results}
        self.assertGreaterEqual(len(winners), 3)

    def test_cross_rank_diagonal_is_first(self):
        results = compare_profiles(self.market, BASE, list(self.profiles.values()))
        table = cross_ranks(results)
        for profile_id, row in table.items():
            self.assertEqual(row[profile_id], 1)

    def test_business_winner_satisfies_business_constraints(self):
        results = compare_profiles(self.market, BASE, [self.profiles["business"]])
        offer = results[0].result.best.offer
        self.assertTrue(offer.fare.is_changeable)
        self.assertEqual(offer.transfers, 0)


if __name__ == "__main__":
    unittest.main()
