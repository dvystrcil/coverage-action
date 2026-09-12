#!/usr/bin/env python3
"""Parse test count and coverage percentages out of a test run's output.

Kept separate from coverage_gate.py, and tested, because this is where a
coverage gate most easily lies. A parser that fails to find the numbers has
two honest options -- report nothing, or report zero -- and they are not the
same. Reporting zero turns "I could not read the report" into "your coverage
is 0%", which then reads as a coverage failure and sends someone to write
tests for a problem that does not exist. So a miss returns None, and the gate
treats a missing metric as unmeasured rather than as zero.

The test COUNT is parsed with the same care and for a stronger reason: it is
the denominator. `tests=0` must be distinguishable from "the line was not
found", because a harness that executed nothing is the failure mode this
whole action exists to catch (homelab#1184).

Supported today:

  node   `node --experimental-test-coverage --test ...`
         Counts come from the TAP-ish summary (`# tests N` / `ℹ tests N`);
         percentages from the `all files` row of the coverage table.

  python `coverage json` output plus unittest's `Ran N tests` line.
         coverage.py reports no function-level percentage, so functions is
         None rather than 0 -- see above.
"""
from __future__ import annotations

import json
import re

# `ℹ tests 249` or `# tests 249`. Anchored to the summary line so a test
# NAMED "tests 5" cannot be mistaken for the count.
NODE_TESTS_RE = re.compile(r'^[#ℹ]\s*tests\s+(\d+)\s*$', re.M)
# `ℹ all files | 99.90 | 87.01 | 96.49 |`
NODE_ALLFILES_RE = re.compile(
    r'^[#ℹ]?\s*all files\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)',
    re.M | re.I)
# unittest: `Ran 14 tests in 0.006s`
PY_TESTS_RE = re.compile(r'^Ran\s+(\d+)\s+tests?\s+in\b', re.M)
# pytest summary: `105 passed in 0.72s`, `3 failed, 102 passed in 0.9s`,
# `1 failed, 2 passed, 1 skipped in 0.1s`. Counted as the SUM of outcomes,
# because the denominator this gate cares about is "how many tests ran", not
# "how many passed" -- a suite where everything failed still ran.
PYTEST_OUTCOME_RE = re.compile(
    r'(\d+)\s+(passed|failed|xfailed|xpassed|error|errors|skipped)\b')
PYTEST_SUMMARY_LINE_RE = re.compile(r'^=*\s*[\d]+\s+\w+.*\bin\s[\d.]+s',
                                    re.M)


def parse_node(output: str) -> dict:
    tests = NODE_TESTS_RE.search(output)
    cov = NODE_ALLFILES_RE.search(output)
    return {
        "tests": int(tests.group(1)) if tests else None,
        "lines": float(cov.group(1)) if cov else None,
        "branches": float(cov.group(2)) if cov else None,
        "functions": float(cov.group(3)) if cov else None,
    }


def _pytest_count(output: str) -> int | None:
    """Sum the outcomes on pytest's summary line, if there is one.

    Scoped to the summary line rather than the whole output, so a test NAMED
    `test_5_passed_records` cannot be scraped as a count, and so a progress
    line like `72%` is never mistaken for one.
    """
    total = 0
    found = False
    for line in PYTEST_SUMMARY_LINE_RE.findall(output):
        for count, _outcome in PYTEST_OUTCOME_RE.findall(line):
            total += int(count)
            found = True
    return total if found else None


def parse_python(test_output: str, coverage_json: str | None) -> dict:
    # Several suites may run in one job; unittest prints one `Ran N` per
    # suite, so the count is their sum, not the last one seen.
    tests = sum(int(m) for m in PY_TESTS_RE.findall(test_output)) or None
    if tests is None:
        # pytest is the other runner in this account's repos, and it reports
        # nothing resembling `Ran N tests`. Without this, a pytest suite of
        # any size parses as zero and the gate reports "NO TESTS RAN" --
        # turning a healthy repo into a hard failure and, worse, teaching
        # whoever sees it that the message means nothing.
        tests = _pytest_count(test_output)
    lines = branches = None
    if coverage_json:
        try:
            totals = json.loads(coverage_json).get("totals", {})
        except json.JSONDecodeError:
            totals = {}
        pct = totals.get("percent_covered")
        if pct is not None:
            lines = float(pct)
        covered = totals.get("covered_branches")
        total = totals.get("num_branches")
        if isinstance(covered, int) and isinstance(total, int) and total > 0:
            branches = 100.0 * covered / total
    return {
        "tests": tests,
        "lines": lines,
        "branches": branches,
        # coverage.py has no function-level percentage. None, never 0.
        "functions": None,
    }


def to_args(metrics: dict) -> list[str]:
    """Render metrics as coverage_gate.py CLI flags, omitting what is absent."""
    out = ["--tests-run", str(metrics.get("tests") or 0)]
    for name in ("lines", "branches", "functions"):
        value = metrics.get(name)
        if value is not None:
            out += [f"--{name}", f"{value:.2f}"]
    return out


if __name__ == "__main__":
    import argparse
    import pathlib
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", choices=("node", "python"), required=True)
    ap.add_argument("--test-output", required=True, type=pathlib.Path)
    ap.add_argument("--coverage-json", type=pathlib.Path)
    a = ap.parse_args()

    text = a.test_output.read_text(encoding="utf-8", errors="replace")
    if a.runtime == "node":
        m = parse_node(text)
    else:
        cj = a.coverage_json.read_text(encoding="utf-8") \
            if a.coverage_json and a.coverage_json.exists() else None
        m = parse_python(text, cj)
    print(" ".join(to_args(m)))
    sys.exit(0)
