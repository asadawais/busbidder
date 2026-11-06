"""Tests for the guided interactive mode, driven by scripted answers."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from busbidder import config
from busbidder.interactive import run_interactive


class TestInteractive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.bookings = tmp / "bookings.json"
        self.patches = [
            mock.patch.object(config, "QUARANTINE_DIR", tmp / "quarantine"),
            mock.patch.object(config, "BOOKINGS_FILE", self.bookings),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self._tmp.cleanup()

    def run_dialogue(self, answers):
        answers = iter(answers)
        output = []
        code = run_interactive(config.DATA_DIR, config.DATA_DIR / "profiles.json",
                               ask=lambda prompt: next(answers), say=output.append)
        return code, "\n".join(output)

    def test_full_search_and_booking(self):
        code, out = self.run_dialogue([
            "Malaga",       # destination
            "2027-07-12",   # first date
            "2027-07-14",   # last date
            "",             # passengers (default 1)
            "50",           # budget
            "1",            # max transfers
            "y",            # changeable
            "n",            # refundable
            "",             # bags
            "",             # max hours
            "1",            # default weights
            "morning",      # departure window
            "1",            # book rank 1
            "Asad Awais",   # passenger name
            "n",            # search again?
        ])
        self.assertEqual(code, 0)
        self.assertIn("Offers that satisfy every hard constraint: 8", out)
        self.assertIn("WHY THIS RECOMMENDATION", out)
        self.assertIn("BOOKING CONFIRMATION", out)
        self.assertTrue(self.bookings.exists())

    def test_invalid_answers_are_asked_again(self):
        code, out = self.run_dialogue([
            "Paris", "7",                    # unknown city, then number 7 (Malaga)
            "12/07/2027", "2027-07-12",      # bad date format, then valid
            "", "0", "",                     # last date default, passengers 0 then default
            "-5", "", "9", "", "maybe", "n", "n", "", "",
            "99", "3",                       # invalid menu choice, then business profile
            "",                              # keep the profile's departure window
            "",                              # do not book
            "n",
        ])
        self.assertEqual(code, 0)
        self.assertIn("Unknown destination 'Paris'", out)
        self.assertIn("YYYY-MM-DD", out)
        self.assertIn("from 1 to 9", out)
        self.assertIn("above 0", out)
        self.assertIn("Profile: business", out)
        self.assertIn("No booking made", out)
        self.assertFalse(self.bookings.exists())

    def test_custom_weights_with_retry(self):
        code, out = self.run_dialogue([
            "SAL", "", "", "", "", "", "n", "n", "", "",
            "6", "price=50,duration=50",                     # invalid: core criteria missing
            "price=30,duration=20,departure=10,flexibility=20,emissions=20",
            "", "", "n",
        ])
        self.assertEqual(code, 0)
        self.assertIn("Core criteria", out)
        self.assertIn("emissions 20%", out)


if __name__ == "__main__":
    unittest.main()
