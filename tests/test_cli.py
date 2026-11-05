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
        ]
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

    def test_invalid_weights_give_friendly_error(self):
        code, _, err = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                    "--weights", "price=50,duration=25")
        self.assertEqual(code, 1)
        self.assertIn("Core criteria", err)

    def test_no_matching_offers(self):
        code, out, _ = self.run_cli("search", "--to", "Malaga", "--from", "2027-07-12",
                                    "--budget", "1")
        self.assertEqual(code, 0)
        self.assertIn("No offer satisfies", out)


if __name__ == "__main__":
    unittest.main()
