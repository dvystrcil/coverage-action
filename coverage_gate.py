#!/usr/bin/env python3
"""Gate a PR on test coverage — and on the tests having actually run.

WHY THIS EXISTS, AND WHY IT CHECKS THREE THINGS RATHER THAN ONE

A coverage percentage answers "how much of the code did the tests touch".
That is worth knowing, and it is not the question a reviewer thinks they are
asking. Two failure modes sit on either side of it, and both were found in
this homelab in one sitting (2026-09-12):

  1. THE SUITE THAT RAN NOTHING.
     A pytest-style module executed as `python3 file.py` imports cleanly,
     defines its test functions, runs none of them and exits 0. The check
     goes green having asserted nothing. Coverage of such a run is not
     "low" in a way anyone notices -- it is simply a number nobody reads.
     So this gate refuses to pass when the test count is zero, and says so
     in different words from "coverage too low", because they are different
     findings (homelab#1184 AC1: a check that can be a no-op must report
     which it was).

  2. THE SUITE THAT RAN EVERYTHING AND CHECKED NOTHING.
     dvystrcil/n8n-workflow measured 99.90% line coverage while five real
     mutations survived: a null-guard whose `&&` could become `||`, a
     staleness comparison whose `>` could become `>=`, a `return false`
     that could become `return true` and would have fired an alert rule on
     every paused workflow in the fleet. Every one of those lines was
     covered. Coverage cannot see any of it.

     This action does NOT attempt mutation testing. That is a deliberate
     scope line, not an oversight -- see the README. What it does do is
     refuse to let anyone read a coverage number as a statement about
     correctness, by reporting lines, branches and functions separately:
     that repo's line coverage was 99.90% and its branch coverage 86.81%,
     and the gap is where the survivors lived.

THE RATCHET

Floors live in a committed baseline file, not in workflow YAML, so raising
one is a reviewable diff. The gate fails when coverage drops below the
baseline and reports -- without failing -- when it has risen enough that
the baseline should be raised. A floor that can only be edited deliberately
is the same shape as homelab's policy/*.yaml exemption lists.

Exit codes, deliberately distinct:

    0  at or above the baseline
    1  below the baseline — coverage regressed
    2  could not measure — no tests ran, or the report could not be parsed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_BELOW_BASELINE = 1
EXIT_CANNOT_MEASURE = 2

COMPLETION_MARKER = "COVERAGE-GATE-COMPLETE"

# How far above the baseline a metric must sit before we suggest raising it.
# Small enough to keep the ratchet moving, large enough that normal churn
# does not produce a suggestion on every PR.
RATCHET_SLACK = 1.0

METRICS = ("lines", "branches", "functions")


class CannotMeasure(RuntimeError):
    """The run produced no usable measurement.

    Distinct from "coverage is low". A caller that conflates them turns a
    broken harness into a passing check the moment the number reads 0.
    """


def load_baseline(path: Path) -> dict:
    """Read the committed floors. A missing file is not a free pass."""
    if not path.exists():
        raise CannotMeasure(
            f"baseline {path} is missing. It holds the floors this gate "
            f"enforces; without it every threshold would silently be zero and "
            f"the gate would pass on any coverage at all."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CannotMeasure(f"baseline {path} is not valid JSON: {exc}") from exc
    floors = data.get("floors")
    if not isinstance(floors, dict) or not floors:
        raise CannotMeasure(f"baseline {path} declares no floors")
    return data


def compare(measured: dict, floors: dict) -> tuple[list, list]:
    """Returns (regressions, ratchet_candidates)."""
    regressions, ratchet = [], []
    for metric in METRICS:
        floor = floors.get(metric)
        got = measured.get(metric)
        if floor is None or got is None:
            continue
        # Both sides rounded to the precision these tools actually report.
        # An earlier version compared `got + 1e-9 < floor`, which is not
        # wrong so much as unfalsifiable: the epsilon made `<` and `<=`
        # behave identically for every input, so no test could tell the two
        # apart and the comparison's intent was unstated. Rounding says the
        # intent out loud -- the floor is INCLUSIVE -- and is checkable.
        got, floor = round(got, 2), round(floor, 2)
        if got < floor:
            regressions.append((metric, got, floor))
        elif got >= floor + RATCHET_SLACK:
            ratchet.append((metric, got, floor))
    return regressions, ratchet


def report(measured: dict, tests_run: int, baseline_path: Path,
           floors: dict) -> int:
    if tests_run <= 0:
        print(
            "::error::NO TESTS RAN. This is not a coverage failure -- the "
            "harness did not execute anything, which a coverage number alone "
            "would report as 0% and a reader could mistake for 'poorly "
            "tested'. Check that the runner actually collects and executes "
            "these files (a pytest-style module run as `python3 file.py` "
            "exits 0 having run nothing).",
            file=sys.stderr)
        print(f"{COMPLETION_MARKER} tests=0 lines=- branches=- functions=- "
              f"verdict=cannot-measure")
        return EXIT_CANNOT_MEASURE

    regressions, ratchet = compare(measured, floors)

    for metric, got, floor in regressions:
        print(f"::error::{metric} coverage {got:.2f}% is below the baseline "
              f"{floor:.2f}% in {baseline_path}")
    for metric, got, floor in ratchet:
        print(f"::notice::{metric} coverage {got:.2f}% is comfortably above "
              f"its {floor:.2f}% floor — raise it in {baseline_path} so the "
              f"gain cannot be lost silently")

    print(f"{COMPLETION_MARKER} tests={tests_run} "
          + " ".join(
              f"{m}={measured[m]:.2f}" if measured.get(m) is not None else f"{m}=-"
              for m in METRICS)
          + f" regressions={len(regressions)} ratchet_ready={len(ratchet)} "
            f"verdict={'fail' if regressions else 'pass'}")
    return EXIT_BELOW_BASELINE if regressions else EXIT_OK


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--tests-run", required=True, type=int)
    ap.add_argument("--lines", type=float)
    ap.add_argument("--branches", type=float)
    ap.add_argument("--functions", type=float)
    args = ap.parse_args(argv[1:])

    measured = {"lines": args.lines, "branches": args.branches,
                "functions": args.functions}
    try:
        baseline = load_baseline(args.baseline)
    except CannotMeasure as exc:
        print(f"::error::{exc}", file=sys.stderr)
        print(f"{COMPLETION_MARKER} tests={args.tests_run} verdict=cannot-measure")
        return EXIT_CANNOT_MEASURE

    return report(measured, args.tests_run, args.baseline, baseline["floors"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
