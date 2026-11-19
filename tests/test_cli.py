"""Smoke tests for the command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from busbidder import cli, config


class TestCli(unittest.TestCase):
    def setUp(self):
        # Keep logs and quarantine files out of the project folder during tests
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.patches = [
            mock.patch.object(config, "LOG_FILE", tmp / "test.log"),
            mock.patch.object(config, "QUARANTINE_DIR", tmp / "quarantine"),
            mock.patch.object(config, "BOOKINGS_FILE", tmp / "bookings.json"),
        ]
        self.bookings_file = tmp / "bookings.json"
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self._tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_validate(self):
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertIn("Rejected", out)

    def test_search_example_request(self):
        code, out, _ = self.run_cli(
            "search", "--to", "Malaga", "--from", "2027-07-12", "--until", "2027-07-14",
            "--budget", "50", "--depart", "morning", "--max-transfers", "1", "--changeable")
        self.assertEqual(code, 0)
        self.assertIn("HARD CONSTRAINTS", out)
        self.assertIn("SOFT PREFERENCES", out)
        self.assertIn("TOTAL", out)

    def test_unknown_destination_gives_friendly_error(self):
        code, _, err = self.run_cli("search", "--to", "Paris", "--from", "2027-07-12")
        self.assertEqual(code, 1)
        self.assertIn("Unknown destination", err)

    def test_invalid_values_give_friendly_errors(self):
        cases = [(("--weights", "price=50,duration=25"), "Core criteria"),
                 (("--budget", "nan"), "Maximum price"),
                 (("--max-hours", "nan"), "Maximum journey time")]
        for extra, message in cases:
            code, _, err = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                        *extra)
            self.assertEqual(code, 1, msg=extra)
            self.assertIn(message, err)

    def test_no_matching_offers(self):
        code, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                    "--budget", "1")
        self.assertEqual(code, 0)
        self.assertIn("No offer satisfies", out)

    def test_search_shows_explanation(self):
        _, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                 "--depart", "morning")
        self.assertIn("WHY THIS RECOMMENDATION", out)
        self.assertIn("Advantages", out)

    def test_search_with_profile(self):
        code, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                    "--profile", "business")
        self.assertEqual(code, 0)
        self.assertIn("Profile: business", out)
        self.assertIn("Ticket must be changeable", out)

    def test_profile_and_weights_together_rejected(self):
        code, _, err = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                    "--profile", "eco", "--weights", "price=100")
        self.assertEqual(code, 1)
        self.assertIn("not both", err)

    def test_compare(self):
        code, out, _ = self.run_cli("compare", "--to", "Malaga", "--from", "2027-07-12",
                                    "--until", "2027-07-14")
        self.assertEqual(code, 0)
        self.assertIn("SUMMARY", out)
        self.assertIn("BUSINESS TRAVELLER", out)

    def test_search_and_book_interactively(self):
        answers = iter(["abc", "1", "Asad Awais"])
        with mock.patch("builtins.input", lambda prompt="": next(answers)):
            code, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                        "--book")
        self.assertEqual(code, 0)
        self.assertIn("Please enter a number", out)
        self.assertIn("BOOKING CONFIRMATION", out)
        self.assertTrue(self.bookings_file.exists())

    def test_skip_booking(self):
        with mock.patch("builtins.input", lambda prompt="": ""):
            code, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                        "--book")
        self.assertEqual(code, 0)
        self.assertIn("No booking made", out)
        self.assertFalse(self.bookings_file.exists())

    def test_book_list_and_cancel(self):
        code, out, _ = self.run_cli("book", "SOL-MAL-20270712-1000-B", "--name", "Asad Awais")
        self.assertEqual(code, 0)
        reference = next(line.split()[-1] for line in out.splitlines()
                         if "Booking reference" in line)
        _, listing, _ = self.run_cli("bookings")
        self.assertIn(reference, listing)
        code, out, _ = self.run_cli("cancel", reference)
        self.assertEqual(code, 0)
        self.assertIn("cancelled", out)

    def test_cancel_bad_reference(self):
        code, _, err = self.run_cli("cancel", "nonsense")
        self.assertEqual(code, 1)
        self.assertIn("not a valid booking reference", err)


if __name__ == "__main__":
    unittest.main()
