"""Tests for the seeded data generator: reproducibility and coverage."""

import tempfile
import unittest
from pathlib import Path

from busbidder.data_generator import COMPANIES, DESTINATIONS, generate_rows, write_dataset
from busbidder.loader import load_marketplace


class TestGenerator(unittest.TestCase):
    def test_same_seed_gives_identical_files(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            write_dataset(Path(a), seed=7)
            write_dataset(Path(b), seed=7)
            for name in ("companies.json", "destinations.csv", "offers.csv",
                         "injected_faults.json"):
                self.assertEqual((Path(a) / name).read_bytes(),
                                 (Path(b) / name).read_bytes(), name)

    def test_different_seed_gives_different_offers(self):
        self.assertNotEqual(generate_rows(1), generate_rows(2))

    def test_all_75_company_destination_combinations_exist(self):
        pairs = {(r["company_id"], r["destination_id"]) for r in generate_rows(42)}
        self.assertEqual(len(COMPANIES), 5)
        self.assertEqual(len(DESTINATIONS), 15)
        self.assertEqual(len(pairs), 75)

    def test_clean_dataset_has_no_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(Path(tmp), seed=42, with_faults=False)
            result = load_marketplace(Path(tmp), quarantine_dir=None)
            self.assertEqual(result.report.rejected, 0)
            self.assertEqual(result.report.review, 0)
            self.assertEqual(result.report.corrected_records, 0)

    def test_overnight_company_departs_in_the_evening(self):
        hours = {int(r["departure_time"][:2]) for r in generate_rows(42)
                 if r["company_id"] == "NOC"}
        self.assertTrue(all(h >= 20 for h in hours))

    def test_nochevia_does_not_report_emissions(self):
        values = {r["co2_kg"] for r in generate_rows(42) if r["company_id"] == "NOC"}
        self.assertEqual(values, {""})


if __name__ == "__main__":
    unittest.main()
