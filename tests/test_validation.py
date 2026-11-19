"""Tests for single-record validation: normal, invalid, missing and boundary cases."""

import unittest
from datetime import datetime

from busbidder.exceptions import (
    InconsistentRecordError,
    InvalidValueError,
    MissingFieldError,
    SuspiciousRecordError,
    UnknownReferenceError,
)
from busbidder.models import ChangePolicy, FareClass, RefundPolicy
from busbidder.validation import (
    OFFER_ID_RE,
    OfferValidator,
    parse_bool,
    parse_date,
    validate_destination,
)
from tests.helpers import COMPANIES, DESTINATIONS, valid_row


class OfferValidatorTestCase(unittest.TestCase):
    def setUp(self):
        self.validator = OfferValidator(COMPANIES, DESTINATIONS)

    def validate(self, **overrides):
        return self.validator.validate(valid_row(**overrides))


class TestValidOffer(OfferValidatorTestCase):
    def test_valid_row_is_converted_to_typed_offer(self):
        offer, corrections = self.validate()
        self.assertEqual(corrections, [])
        self.assertEqual(offer.company.name, "SolRuta")
        self.assertEqual(offer.destination.name, "Malaga")
        self.assertEqual(offer.departure, datetime(2027, 7, 12, 8, 30))
        self.assertEqual(offer.duration_min, 435)
        self.assertEqual(offer.price_eur, 34.99)
        self.assertEqual(offer.transfers, 1)
        self.assertIs(offer.fare.fare_class, FareClass.STANDARD)
        self.assertIs(offer.fare.change_policy, ChangePolicy.FEE)
        self.assertIs(offer.fare.refund_policy, RefundPolicy.NONE)
        self.assertTrue(offer.fare.is_changeable)
        self.assertFalse(offer.fare.is_refundable)

    def test_overnight_arrival_on_next_day(self):
        offer, _ = self.validate(
            offer_id="SOL-MAL-20270712-2230-S", departure_time="22:30",
            arrival_date="2027-07-13", arrival_time="05:45", duration_min="435",
        )
        self.assertEqual(offer.duration_min, 435)
        self.assertEqual(offer.travel_date.isoformat(), "2027-07-12")


class TestRejectedOffers(OfferValidatorTestCase):
    def test_negative_price(self):
        with self.assertRaises(InvalidValueError) as ctx:
            self.validate(price_eur="-12.50")
        self.assertEqual(ctx.exception.field, "price_eur")

    def test_zero_price_is_rejected(self):
        with self.assertRaises(InvalidValueError):
            self.validate(price_eur="0")

    def test_non_numeric_price(self):
        # float() accepts "nan" and "inf", so they need their own check
        for value in ("cheap", "nan", "NaN", "inf", "-inf"):
            with self.assertRaises(InvalidValueError, msg=value):
                self.validate(price_eur=value)

    def test_unknown_destination(self):
        with self.assertRaises(UnknownReferenceError):
            self.validate(destination_id="XYZ")

    def test_unknown_company(self):
        with self.assertRaises(UnknownReferenceError):
            self.validate(company_id="ABC")

    def test_invalid_calendar_date(self):
        with self.assertRaises(InvalidValueError):
            self.validate(travel_date="2027-02-30")

    def test_wrong_date_format(self):
        with self.assertRaises(InvalidValueError):
            self.validate(travel_date="12/07/2027")

    def test_invalid_clock_time(self):
        with self.assertRaises(InvalidValueError):
            self.validate(departure_time="25:10")

    def test_arrival_before_departure(self):
        with self.assertRaises(InconsistentRecordError):
            self.validate(arrival_time="06:00")

    def test_arrival_equal_to_departure(self):
        with self.assertRaises(InconsistentRecordError):
            self.validate(arrival_time="08:30")

    def test_negative_seats(self):
        with self.assertRaises(InvalidValueError):
            self.validate(seats_available="-1")

    def test_invalid_category(self):
        with self.assertRaises(InvalidValueError):
            self.validate(refund_policy="maybe")

    def test_malformed_offer_id(self):
        with self.assertRaises(InvalidValueError):
            self.validate(offer_id="SOL_MAL_20270712_0830_S")

    def test_offer_id_disagrees_with_record(self):
        with self.assertRaises(InconsistentRecordError):
            self.validate(offer_id="SOL-MAL-20270712-0900-S")

    def test_too_many_transfers(self):
        with self.assertRaises(InvalidValueError):
            self.validate(transfers="4")

    def test_negative_co2(self):
        with self.assertRaises(InvalidValueError):
            self.validate(co2_kg="-1")


class TestMissingData(OfferValidatorTestCase):
    def test_missing_required_price(self):
        with self.assertRaises(MissingFieldError):
            self.validate(price_eur="")

    def test_whitespace_only_counts_as_missing(self):
        with self.assertRaises(MissingFieldError):
            self.validate(destination_id="   ")

    def test_missing_fee_when_policy_is_fee(self):
        with self.assertRaises(MissingFieldError):
            self.validate(change_fee_eur="")

    def test_missing_optional_rating_is_allowed(self):
        offer, _ = self.validate(rating="")
        self.assertIsNone(offer.rating)

    def test_missing_optional_co2_is_allowed(self):
        offer, _ = self.validate(co2_kg="")
        self.assertIsNone(offer.co2_kg)


class TestCorrections(OfferValidatorTestCase):
    def test_comma_decimal_price_is_corrected(self):
        offer, corrections = self.validate(price_eur="34,99")
        self.assertEqual(offer.price_eur, 34.99)
        self.assertEqual(corrections[0][0], "price_eur")

    def test_category_case_is_corrected(self):
        offer, corrections = self.validate(change_policy="FEE")
        self.assertIs(offer.fare.change_policy, ChangePolicy.FEE)
        self.assertEqual(len(corrections), 1)

    def test_wrong_duration_is_recomputed(self):
        offer, corrections = self.validate(duration_min="480")
        self.assertEqual(offer.duration_min, 435)
        self.assertEqual(corrections[0][0], "duration_min")

    def test_fee_ignored_when_changes_are_free(self):
        offer, corrections = self.validate(change_policy="free", change_fee_eur="5.00")
        self.assertEqual(offer.fare.change_fee_eur, 0.0)
        self.assertEqual(corrections[0][0], "change_fee_eur")


class TestReview(OfferValidatorTestCase):
    def test_implausible_speed_goes_to_review(self):
        with self.assertRaises(SuspiciousRecordError):
            self.validate(arrival_time="10:30", duration_min="120")  # 530 km in 2 h

    def test_extreme_price_goes_to_review(self):
        with self.assertRaises(SuspiciousRecordError):
            self.validate(price_eur="389.00")

    def test_impossible_beats_suspicious(self):
        # A record that is both suspicious and invalid is rejected, not reviewed
        with self.assertRaises(InvalidValueError):
            self.validate(price_eur="389.00", rating="9")


class TestBoundaries(OfferValidatorTestCase):
    def test_rating_scale_edges_are_accepted(self):
        for value in ("1.0", "5.0"):
            offer, _ = self.validate(rating=value)
            self.assertEqual(offer.rating, float(value))

    def test_rating_just_outside_scale_is_rejected(self):
        for value in ("0.9", "5.1"):
            with self.assertRaises(InvalidValueError):
                self.validate(rating=value)

    def test_zero_seats_is_valid_but_sold_out(self):
        offer, _ = self.validate(seats_available="0")
        self.assertTrue(offer.is_sold_out)

    def test_smallest_valid_price(self):
        offer, _ = self.validate(price_eur="0.01")
        self.assertEqual(offer.price_eur, 0.01)

    def test_duration_within_tolerance_is_not_corrected(self):
        _, corrections = self.validate(duration_min="436")
        self.assertEqual(corrections, [])

    def test_speed_exactly_at_limit_is_accepted(self):
        # Toledo 72 km in 40 min = 108 km/h, just under the 110 km/h limit
        offer, _ = self.validate(
            offer_id="SOL-TOL-20270712-0830-S", destination_id="TOL",
            arrival_time="09:10", duration_min="40",
        )
        self.assertEqual(offer.duration_min, 40)


class TestParsersAndPatterns(unittest.TestCase):
    def test_offer_id_pattern(self):
        self.assertTrue(OFFER_ID_RE.match("CAM-BAR-20270714-0630-F"))
        self.assertFalse(OFFER_ID_RE.match("CAM-BAR-20270714-0630-X"))
        self.assertFalse(OFFER_ID_RE.match("cam-BAR-20270714-0630-F"))
        self.assertFalse(OFFER_ID_RE.match("CAM-BAR-2027071-0630-F"))

    def test_parse_bool(self):
        self.assertTrue(parse_bool("yes", "x"))
        self.assertFalse(parse_bool("No", "x"))
        with self.assertRaises(InvalidValueError):
            parse_bool("perhaps", "x")

    def test_leap_day(self):
        self.assertEqual(parse_date("2028-02-29", "d").day, 29)
        with self.assertRaises(InvalidValueError):
            parse_date("2027-02-29", "d")

    def test_destination_distance_must_be_positive(self):
        with self.assertRaises(InvalidValueError):
            validate_destination({"code": "AAA", "name": "A", "distance_km": "0",
                                  "coastal": "no", "region": ""})


class TestOfferSeats(OfferValidatorTestCase):
    def test_reserve_reduces_capacity(self):
        offer, _ = self.validate(seats_available="2")
        offer.reserve_seats(2)
        self.assertEqual(offer.seats_available, 0)

    def test_cannot_reserve_more_than_available(self):
        from busbidder.exceptions import SeatUnavailableError
        offer, _ = self.validate(seats_available="1")
        with self.assertRaises(SeatUnavailableError):
            offer.reserve_seats(2)
        self.assertEqual(offer.seats_available, 1)


if __name__ == "__main__":
    unittest.main()
