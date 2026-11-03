"""Tests for file loading, the reject/correct/review routing and quarantine files."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from busbidder import config
from busbidder.data_generator import write_dataset as generate_dataset
from busbidder.exceptions import DataFileError
from busbidder.loader import DataLoader, load_marketplace
from busbidder.validation import OFFER_FIELDS, Action
from tests.helpers import valid_row, write_dataset


class LoaderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.quarantine = self.tmp / "quarantine"

    def tearDown(self):
        self._tmp.cleanup()

    def load(self):
        return load_marketplace(self.data, quarantine_dir=self.quarantine)


class TestNormalLoading(LoaderTestCase):
    def test_valid_rows_are_loaded(self):
        write_dataset(self.data, [
            valid_row(),
            valid_row(offer_id="SOL-MAL-20270712-0830-B", fare_class="B",
                      change_policy="none", change_fee_eur=""),
        ])
        result = self.load()
        self.assertEqual(len(result.marketplace.offers), 2)
        self.assertEqual(result.report.accepted, 2)
        self.assertEqual(result.report.rejected, 0)
        self.assertEqual(len(result.marketplace.companies), 2)
        self.assertEqual(len(result.marketplace.destinations), 2)

    def test_find_destination_by_name_or_code(self):
        write_dataset(self.data, [valid_row()])
        market = self.load().marketplace
        self.assertEqual(market.find_destination("malaga").code, "MAL")
        self.assertEqual(market.find_destination("mal").code, "MAL")
        self.assertIsNone(market.find_destination("Paris"))


class TestRecordRouting(LoaderTestCase):
    def test_invalid_records_are_rejected_and_quarantined(self):
        write_dataset(self.data, [
            valid_row(),
            valid_row(offer_id="SOL-MAL-20270712-0830-B", fare_class="B",
                      price_eur="-5"),
            valid_row(),  # duplicate id
        ])
        result = self.load()
        self.assertEqual(result.report.accepted, 1)
        self.assertEqual(result.report.rejected, 2)
        with open(self.quarantine / "rejected_offers.csv", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["problem_field"], "price_eur")
        self.assertEqual(rows[1]["reason"], "duplicate offer id")

    def test_suspicious_record_is_held_for_review(self):
        write_dataset(self.data, [valid_row(), valid_row(
            offer_id="SOL-MAL-20270712-0830-B", fare_class="B", price_eur="389.00")])
        result = self.load()
        self.assertEqual(result.report.review, 1)
        self.assertNotIn("SOL-MAL-20270712-0830-B", result.marketplace.offers)
        with open(self.quarantine / "review_offers.csv", newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_corrected_record_is_kept_and_reported(self):
        write_dataset(self.data, [valid_row(price_eur="34,99")])
        result = self.load()
        self.assertEqual(result.report.accepted, 1)
        self.assertEqual(result.report.corrected_records, 1)
        self.assertEqual(result.report.issues[0].action, Action.CORRECTED)

    def test_sold_out_offers_are_loaded_and_counted(self):
        write_dataset(self.data, [valid_row(seats_available="0")])
        result = self.load()
        self.assertEqual(result.report.sold_out, 1)
        self.assertEqual(result.report.accepted, 1)


class TestFileErrors(LoaderTestCase):
    def test_missing_offers_file(self):
        write_dataset(self.data, [valid_row()])
        (self.data / "offers.csv").rename(self.data / "other.csv")
        with self.assertRaises(DataFileError):
            self.load()

    def test_missing_data_folder(self):
        with self.assertRaises(DataFileError):
            load_marketplace(self.tmp / "nowhere", quarantine_dir=None)

    def test_missing_column(self):
        fields = [f for f in OFFER_FIELDS if f != "price_eur"]
        write_dataset(self.data, [valid_row()], offer_fields=fields)
        with self.assertRaises(DataFileError) as ctx:
            self.load()
        self.assertIn("price_eur", str(ctx.exception))

    def test_invalid_json(self):
        write_dataset(self.data, [valid_row()])
        (self.data / "companies.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(DataFileError):
            self.load()

    def test_only_invalid_offers(self):
        write_dataset(self.data, [valid_row(price_eur="-1")])
        with self.assertRaises(DataFileError):
            self.load()

    def test_empty_offers_file(self):
        write_dataset(self.data, [])
        with self.assertRaises(DataFileError):
            self.load()


class TestGeneratedDatasetFaults(unittest.TestCase):
    """The loader must treat each injected fault as documented in the manifest."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        data = Path(cls._tmp.name)
        generate_dataset(data, seed=config.DEFAULT_SEED)
        cls.manifest = json.loads((data / "injected_faults.json").read_text())["faults"]
        cls.result = DataLoader(
            data / "companies.json", data / "destinations.csv", data / "offers.csv",
            quarantine_dir=None,
        ).load()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def expected(self, action):
        return sum(1 for f in self.manifest if f["expected_action"] == action)

    def test_counts_match_manifest(self):
        report = self.result.report
        self.assertEqual(report.rejected, self.expected("rejected"))
        self.assertEqual(report.review, self.expected("review"))
        self.assertEqual(report.corrected_records, self.expected("corrected"))

    def test_each_corrected_fault_is_reported(self):
        corrected_ids = {i.record_id for i in self.result.report.issues
                         if i.action is Action.CORRECTED}
        for fault in self.manifest:
            if fault["expected_action"] == "corrected":
                self.assertIn(fault["offer_id"], corrected_ids, fault["fault"])

    def test_missing_ratings_are_accepted(self):
        for fault in self.manifest:
            if fault["expected_action"] == "accepted":
                offer = self.result.marketplace.get_offer(fault["offer_id"])
                self.assertIsNotNone(offer)
                self.assertIsNone(offer.rating)


if __name__ == "__main__":
    unittest.main()
