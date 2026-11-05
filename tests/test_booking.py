"""Tests for simulated booking: seats, references, persistence and cancellation."""

import json
import random
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from busbidder.booking import (
    CANCELLED,
    REFERENCE_RE,
    BookingManager,
    validate_name,
    validate_reference,
)
from busbidder.exceptions import (
    BookingError,
    BookingNotFoundError,
    DataFileError,
    SeatUnavailableError,
)
from busbidder.models import Marketplace
from tests.helpers import COMPANIES, DESTINATIONS, make_offer


def fresh_market():
    market = Marketplace(companies=dict(COMPANIES), destinations=dict(DESTINATIONS))
    market.add_offer(make_offer("SOL-MAL-20270712-0830-S", seats=3, change="fee",
                                refund="partial", price=40.0))
    market.add_offer(make_offer("SOL-MAL-20270712-0830-B", seats=1, change="none",
                                refund="none", fare="B", price=30.0))
    return market


class BookingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "bookings.json"
        self.clock = lambda: datetime(2026, 11, 5, 12, 0, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def manager(self, market=None, seed=1):
        return BookingManager(market or fresh_market(), self.path,
                              rng=random.Random(seed), clock=self.clock)


class TestBooking(BookingTestCase):
    def test_booking_reduces_seats_and_saves(self):
        manager = self.manager()
        booking = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais", passengers=2)
        self.assertTrue(REFERENCE_RE.match(booking.reference))
        self.assertEqual(booking.total_price_eur, 80.0)
        self.assertEqual(manager.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 1)
        saved = json.loads(self.path.read_text())["bookings"]
        self.assertEqual(saved[0]["reference"], booking.reference)
        self.assertEqual(saved[0]["fare"]["refund_policy"], "partial")
        self.assertIn("simulated", saved[0]["payment_status"])

    def test_fare_conditions_are_stored(self):
        booking = self.manager().book("SOL-MAL-20270712-0830-S", "Asad Awais")
        self.assertEqual(booking.fare["change_policy"], "fee")
        self.assertEqual(booking.fare["change_fee_eur"], 8.0)
        self.assertIn("Standard", booking.fare["summary"])

    def test_cannot_overbook(self):
        manager = self.manager()
        manager.book("SOL-MAL-20270712-0830-B", "First Person")
        with self.assertRaises(SeatUnavailableError):
            manager.book("SOL-MAL-20270712-0830-B", "Second Person")
        self.assertEqual(len(manager.all()), 1)

    def test_last_seat_boundary(self):
        manager = self.manager()
        manager.book("SOL-MAL-20270712-0830-S", "Group Lead", passengers=3)
        self.assertEqual(manager.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 0)

    def test_unknown_offer(self):
        with self.assertRaises(BookingError):
            self.manager().book("SOL-MAL-20270712-0000-S", "Asad Awais")

    def test_invalid_passenger_count(self):
        for count in (0, 10):
            with self.assertRaises(BookingError):
                self.manager().book("SOL-MAL-20270712-0830-S", "Asad Awais", passengers=count)

    def test_references_are_unique(self):
        market = fresh_market()
        market.get_offer("SOL-MAL-20270712-0830-S").seats_available = 50
        manager = self.manager(market)
        refs = {manager.book("SOL-MAL-20270712-0830-S", "Same Person").reference
                for _ in range(30)}
        self.assertEqual(len(refs), 30)

    def test_reference_collision_is_retried(self):
        manager = self.manager(seed=5)
        first = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais").reference
        # Same seed would produce the same reference again; it must be skipped
        manager.rng = random.Random(5)
        second = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais").reference
        self.assertNotEqual(first, second)

    def test_save_failure_rolls_back(self):
        manager = self.manager()
        with mock.patch.object(manager, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(BookingError):
                manager.book("SOL-MAL-20270712-0830-S", "Asad Awais")
        self.assertEqual(manager.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 3)
        self.assertEqual(manager.all(), [])


class TestPersistence(BookingTestCase):
    def test_existing_bookings_reduce_capacity_on_restart(self):
        self.manager().book("SOL-MAL-20270712-0830-S", "Asad Awais", passengers=2)
        restarted = self.manager(fresh_market())  # seats reloaded from "file" = 3
        self.assertEqual(restarted.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 1)
        self.assertEqual(len(restarted.all()), 1)

    def test_cancelled_bookings_do_not_reduce_capacity(self):
        manager = self.manager()
        ref = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais", passengers=2).reference
        manager.cancel(ref)
        restarted = self.manager(fresh_market())
        self.assertEqual(restarted.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 3)

    def test_corrupt_bookings_file(self):
        self.path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(DataFileError):
            self.manager()

    def test_bad_reference_in_file(self):
        self.manager().book("SOL-MAL-20270712-0830-S", "Asad Awais")
        data = json.loads(self.path.read_text())
        data["bookings"][0]["reference"] = "XX-123"
        self.path.write_text(json.dumps(data))
        with self.assertRaises(BookingError):
            self.manager()

    def test_booking_for_removed_offer_is_ignored(self):
        self.manager().book("SOL-MAL-20270712-0830-S", "Asad Awais")
        market = Marketplace(companies=dict(COMPANIES), destinations=dict(DESTINATIONS))
        manager = self.manager(market)  # offer no longer exists
        self.assertEqual(len(manager.all()), 1)


class TestCancellation(BookingTestCase):
    def test_partial_refund_and_seats_released(self):
        manager = self.manager()
        ref = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais", passengers=2).reference
        booking = manager.cancel(ref.lower())  # reference lookup ignores case
        self.assertEqual(booking.status, CANCELLED)
        self.assertEqual(booking.refund_eur, 40.0)  # 50% of EUR 80
        self.assertEqual(manager.market.get_offer("SOL-MAL-20270712-0830-S").seats_available, 3)

    def test_non_refundable_gets_nothing(self):
        manager = self.manager()
        ref = manager.book("SOL-MAL-20270712-0830-B", "Asad Awais").reference
        self.assertEqual(manager.cancel(ref).refund_eur, 0.0)

    def test_cannot_cancel_twice(self):
        manager = self.manager()
        ref = manager.book("SOL-MAL-20270712-0830-S", "Asad Awais").reference
        manager.cancel(ref)
        with self.assertRaises(BookingError):
            manager.cancel(ref)

    def test_unknown_and_malformed_references(self):
        manager = self.manager()
        with self.assertRaises(BookingNotFoundError):
            manager.cancel("BB-ABCDEF")
        with self.assertRaises(BookingError):
            manager.get("12345")


class TestValidators(unittest.TestCase):
    def test_reference_format(self):
        self.assertEqual(validate_reference(" bb-7k3q9x "), "BB-7K3Q9X")
        for bad in ("BB-7K3Q9", "BB-7K3Q9XX", "BB-0K3Q9X", "BB-IK3Q9X", "XX-7K3Q9X"):
            with self.assertRaises(BookingError, msg=bad):
                validate_reference(bad)

    def test_passenger_names(self):
        for good in ("Asad Mohammad Awais", "O'Neil", "Anne-Marie Lund", "Ødegård"):
            self.assertEqual(validate_name(good), good)
        self.assertEqual(validate_name("  Asad   Awais "), "Asad Awais")
        for bad in ("", "A", "Agent 007", "Bob;DROP", "x" * 61):
            with self.assertRaises(BookingError, msg=bad):
                validate_name(bad)


if __name__ == "__main__":
    unittest.main()
