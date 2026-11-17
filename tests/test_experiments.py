"""Tests for the experiment runner and the criterion override used by it."""

import tempfile
import unittest
from pathlib import Path

from busbidder.criteria import MissingPolicy, RelativeScale, get_criterion, overridden
from busbidder.experiments import EXAMPLES_DIR, run_all


class TestOverride(unittest.TestCase):
    def test_override_is_restored(self):
        before = get_criterion("emissions")
        with overridden("emissions", missing=MissingPolicy.NEUTRAL):
            self.assertIs(get_criterion("emissions").missing, MissingPolicy.NEUTRAL)
        self.assertIs(get_criterion("emissions"), before)

    def test_override_is_restored_after_error(self):
        before = get_criterion("price")
        with self.assertRaises(RuntimeError):
            with overridden("price", scale=RelativeScale(True, 0.0, use_fences=False)):
                raise RuntimeError("boom")
        self.assertIs(get_criterion("price"), before)

    def test_plain_min_max_with_equal_values(self):
        score = RelativeScale(True, 0.0, use_fences=False).prepare([20, 20])
        self.assertEqual(score(20), 100)


class TestExperiments(unittest.TestCase):
    def test_all_outputs_written_and_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = run_all(Path(a))
            second = run_all(Path(b))
            self.assertGreaterEqual(len(first), 10)
            for path_a, path_b in zip(first, second):
                self.assertTrue(path_a.exists())
                self.assertEqual(path_a.read_bytes(), path_b.read_bytes(), path_a.name)

    @unittest.skipUnless(EXAMPLES_DIR.exists(), "examples/ has not been generated yet")
    def test_saved_examples_match_a_fresh_run(self):
        """The saved results in examples/ can be reproduced exactly."""
        with tempfile.TemporaryDirectory() as tmp:
            for path in run_all(Path(tmp)):
                saved = EXAMPLES_DIR / path.name
                self.assertTrue(saved.exists(), saved.name)
                self.assertEqual(path.read_bytes(), saved.read_bytes(), saved.name)

    def test_experiments_leave_registry_unchanged(self):
        before = {n: get_criterion(n) for n in ("price", "duration", "emissions")}
        with tempfile.TemporaryDirectory() as tmp:
            run_all(Path(tmp))
        for name, criterion in before.items():
            self.assertIs(get_criterion(name), criterion)


if __name__ == "__main__":
    unittest.main()
