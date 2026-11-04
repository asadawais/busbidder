"""Tests for hard-constraint filtering."""

import unittest
from datetime import date

from busbidder.constraints import active_constraints, apply_constraints, registered_constraints
from busbidder.request import TravelRequest
from tests.helpers import make_offer


def request(**overrides):
    values = dict(destination="MAL", earliest_date=date(2027, 7, 12),
                  latest_date=date(2027, 7, 14))
    values.update(overrides)
    return TravelRequest(**values)


class TestConstraints(unittest.TestCase):
    def check(self, offer, req):
        result = apply_constraints([offer], req)
        return result.reasons.get(offer.offer_id, [])

    def test_acceptable_offer_passes(self):
        self.assertEqual(self.check(make_offer(), request(max_price_eur=50)), [])

    def test_wrong_destination(self):
        offer = make_offer("SOL-TOL-20270712-0830-S", destination="TOL")
        self.assertEqual(self.check(offer, request())[0][0], "destination")

    def test_travel_window_edges(self):
        first = make_offer(departure="2027-07-12 00:00")
        last = make_offer(departure="2027-07-14 23:30")
        before = make_offer(departure="2027-07-11 23:59")
        after = make_offer(departure="2027-07-15 00:00")
        self.assertEqual(self.check(first, request()), [])
        self.assertEqual(self.check(last, request()), [])
        self.assertEqual(self.check(before, request())[0][0], "travel_window")
        self.assertEqual(self.check(after, request())[0][0], "travel_window")

    def test_budget_boundary(self):
        self.assertEqual(self.check(make_offer(price=50.0), request(max_price_eur=50)), [])
        reasons = self.check(make_offer(price=50.01), request(max_price_eur=50))
        self.assertEqual(reasons[0][0], "budget")

    def test_sold_out_and_too_few_seats(self):
        self.assertEqual(self.check(make_offer(seats=0), request())[0][1], "sold out")
        reasons = self.check(make_offer(seats=2), request(passengers=3))
        self.assertEqual(reasons[0][0], "seats")
        self.assertEqual(self.check(make_offer(seats=3), request(passengers=3)), [])

    def test_transfers(self):
        self.assertEqual(self.check(make_offer(transfers=1), request(max_transfers=1)), [])
        self.assertEqual(self.check(make_offer(transfers=2), request(max_transfers=1))[0][0],
                         "max_transfers")

    def test_changeable_and_refundable(self):
        basic = make_offer(change="none", refund="none", fare="B")
        self.assertEqual(self.check(basic, request(require_changeable=True))[0][0], "changeable")
        self.assertEqual(self.check(basic, request(require_refundable=True))[0][0], "refundable")
        flex = make_offer(change="free", refund="full", fare="F")
        self.assertEqual(self.check(flex, request(require_changeable=True,
                                                  require_refundable=True)), [])

    def test_luggage_and_duration(self):
        offer = make_offer(bags=0, duration=500)
        reasons = dict(self.check(offer, request(min_luggage_bags=1, max_duration_min=480)))
        self.assertIn("luggage", reasons)
        self.assertIn("max_duration", reasons)

    def test_unset_constraints_are_not_active(self):
        names = {c.name for c in active_constraints(request())}
        self.assertEqual(names, {"destination", "travel_window", "seats"})

    def test_registry_contains_all_constraints(self):
        names = [c.name for c in registered_constraints()]
        self.assertEqual(names[:3], ["destination", "travel_window", "seats"])
        self.assertIn("budget", names)
        self.assertEqual(len(names), len(set(names)))


class TestFilterSummary(unittest.TestCase):
    def test_each_removed_offer_counted_once_under_first_failure(self):
        offers = [
            make_offer("A", price=30),
            make_offer("B", price=80),  # budget only
            make_offer("C", price=80, change="none", refund="none", fare="B"),  # budget first
            make_offer("D", change="none", refund="none", fare="B"),  # changeable
            make_offer("E", destination="TOL"),  # out of scope
        ]
        result = apply_constraints(offers, request(max_price_eur=50, require_changeable=True))
        self.assertEqual([o.offer_id for o in result.accepted], ["A"])
        self.assertEqual(result.in_scope, 4)
        self.assertEqual(result.removed_by["budget"], 2)
        self.assertEqual(result.removed_by["changeable"], 1)
        self.assertEqual(result.removed_by["destination"], 1)
        # all reasons are kept even though C is counted once
        self.assertEqual(len(result.reasons["C"]), 2)

    def test_empty_input(self):
        result = apply_constraints([], request())
        self.assertEqual(result.accepted, [])
        self.assertEqual(result.in_scope, 0)


if __name__ == "__main__":
    unittest.main()
