"""Spec for coverage_gate.py.

The gate's whole value is that it distinguishes three outcomes a single
percentage cannot:

    0  at or above the baseline
    1  below the baseline — coverage regressed
    2  could not measure — no tests ran, or no baseline

Most of these tests are about exit code 2. A gate that reports "0% coverage"
when the harness executed nothing has turned a broken runner into a number,
and somebody will read that number as "badly tested" and move on.

stdlib unittest, not pytest: CI in this org runs suites as `python3 <file>`,
and a pytest-style module run that way imports cleanly, runs nothing and
exits 0 — the exact defect this action exists to catch. Running the suite
must be self-demonstrating.

Run: python3 tests/test_coverage_gate.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "coverage_gate.py"
_spec = importlib.util.spec_from_file_location("coverage_gate", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def baseline_file(floors: dict) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "baseline.json"
    tmp.write_text(json.dumps({"floors": floors}), encoding="utf-8")
    return tmp


def run(argv: list[str]):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gate.main(["coverage_gate.py"] + argv)
    return code, out.getvalue(), err.getvalue()


FLOORS = {"lines": 90.0, "branches": 80.0, "functions": 85.0}


class TestCannotMeasure(unittest.TestCase):
    """Exit 2 is not a coverage verdict. It means the question was not asked."""

    def test_zero_tests_is_cannot_measure_not_zero_coverage(self):
        bl = baseline_file(FLOORS)
        code, out, err = run(["--baseline", str(bl), "--tests-run", "0",
                              "--lines", "0", "--branches", "0", "--functions", "0"])
        self.assertEqual(code, gate.EXIT_CANNOT_MEASURE)
        self.assertIn("NO TESTS RAN", err)
        self.assertIn("verdict=cannot-measure", out)

    def test_zero_tests_with_high_coverage_still_cannot_measure(self):
        # Defensive: a stale report from a previous run must not launder a
        # harness that executed nothing into a pass.
        bl = baseline_file(FLOORS)
        code, _, _ = run(["--baseline", str(bl), "--tests-run", "0",
                          "--lines", "100", "--branches", "100", "--functions", "100"])
        self.assertEqual(code, gate.EXIT_CANNOT_MEASURE)

    def test_missing_baseline_is_cannot_measure_not_a_free_pass(self):
        missing = Path(tempfile.mkdtemp()) / "nope.json"
        code, out, err = run(["--baseline", str(missing), "--tests-run", "50",
                              "--lines", "99"])
        self.assertEqual(code, gate.EXIT_CANNOT_MEASURE)
        self.assertIn("missing", err)

    def test_baseline_with_no_floors_is_cannot_measure(self):
        tmp = Path(tempfile.mkdtemp()) / "b.json"
        tmp.write_text(json.dumps({"floors": {}}), encoding="utf-8")
        code, _, err = run(["--baseline", str(tmp), "--tests-run", "50",
                            "--lines", "99"])
        self.assertEqual(code, gate.EXIT_CANNOT_MEASURE)
        self.assertIn("no floors", err)

    def test_malformed_baseline_is_cannot_measure(self):
        tmp = Path(tempfile.mkdtemp()) / "b.json"
        tmp.write_text("{not json", encoding="utf-8")
        code, _, err = run(["--baseline", str(tmp), "--tests-run", "1", "--lines", "99"])
        self.assertEqual(code, gate.EXIT_CANNOT_MEASURE)


class TestRegression(unittest.TestCase):

    def test_below_floor_fails(self):
        bl = baseline_file(FLOORS)
        code, _, _ = run(["--baseline", str(bl), "--tests-run", "10",
                          "--lines", "89.99", "--branches", "80", "--functions", "85"])
        self.assertEqual(code, gate.EXIT_BELOW_BASELINE)

    def test_exactly_on_the_floor_passes(self):
        # The floor is inclusive. Off-by-one here would fail every PR that
        # holds coverage exactly steady, which is most of them.
        bl = baseline_file(FLOORS)
        code, _, _ = run(["--baseline", str(bl), "--tests-run", "10",
                          "--lines", "90.0", "--branches", "80.0", "--functions", "85.0"])
        self.assertEqual(code, gate.EXIT_OK)

    def test_a_single_metric_regressing_is_enough_to_fail(self):
        # Branch coverage is where n8n-workflow's five surviving mutants lived
        # while line coverage read 99.90%. Lines passing must not mask it.
        bl = baseline_file(FLOORS)
        code, _, _ = run(["--baseline", str(bl), "--tests-run", "10",
                          "--lines", "100", "--branches", "40", "--functions", "100"])
        self.assertEqual(code, gate.EXIT_BELOW_BASELINE)

    def test_the_failing_metric_is_named(self):
        bl = baseline_file(FLOORS)
        _, out, _ = run(["--baseline", str(bl), "--tests-run", "10",
                         "--lines", "100", "--branches", "40", "--functions", "100"])
        self.assertIn("branches", out)


class TestRatchet(unittest.TestCase):

    def test_comfortably_above_suggests_raising_the_floor(self):
        bl = baseline_file(FLOORS)
        code, out, _ = run(["--baseline", str(bl), "--tests-run", "10",
                            "--lines", "99", "--branches", "95", "--functions", "99"])
        self.assertEqual(code, gate.EXIT_OK, 'a ratchet suggestion must not fail the build')
        self.assertIn("ratchet_ready=3", out)

    def test_barely_above_does_not_nag(self):
        # Suggesting a raise on every PR is how a notice gets ignored.
        bl = baseline_file(FLOORS)
        _, out, _ = run(["--baseline", str(bl), "--tests-run", "10",
                         "--lines", "90.5", "--branches", "80.5", "--functions", "85.5"])
        self.assertIn("ratchet_ready=0", out)


class TestMarker(unittest.TestCase):

    def test_marker_carries_the_denominator(self):
        # homelab#1317: a completion line reporting only faults cannot be told
        # apart from one that examined nothing. tests=N is the denominator.
        bl = baseline_file(FLOORS)
        _, out, _ = run(["--baseline", str(bl), "--tests-run", "42",
                         "--lines", "95", "--branches", "85", "--functions", "90"])
        self.assertIn(gate.COMPLETION_MARKER, out)
        self.assertIn("tests=42", out)

    def test_exit_codes_are_distinct(self):
        self.assertEqual(len({gate.EXIT_OK, gate.EXIT_BELOW_BASELINE,
                              gate.EXIT_CANNOT_MEASURE}), 3)

    def test_absent_metric_is_reported_as_absent_not_zero(self):
        # A language whose tooling reports no branch data must not read as 0%.
        bl = baseline_file({"lines": 90.0})
        code, out, _ = run(["--baseline", str(bl), "--tests-run", "5", "--lines", "95"])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("branches=-", out)


if __name__ == "__main__":
    unittest.main()
