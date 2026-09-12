"""Spec for extract_metrics.py.

This parser is where a coverage gate most easily lies. The invariant under
test throughout: a metric the parser could not find is None, never 0.0.
Reporting zero would turn "I could not read the report" into "your coverage
is 0%", which reads as a coverage failure and sends someone to write tests
for a problem that does not exist.

Fixtures are real output shapes, not invented ones -- the node samples are
trimmed from an actual `node --experimental-test-coverage --test tests/*.js`
run on dvystrcil/n8n-workflow.

Run: python3 tests/test_extract_metrics.py
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "extract_metrics.py"
_spec = importlib.util.spec_from_file_location("extract_metrics", SCRIPT)
em = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(em)

NODE_REAL = """
✔ an inactive workflow is never reported as schedule-disabled (0.2ms)
ℹ tests 249
ℹ suites 0
ℹ pass 249
ℹ fail 0
ℹ start of coverage report
ℹ file                     | line % | branch % | funcs % | uncovered lines
ℹ lib                      |        |          |         |
ℹ  n8n_sync.js             | 100.00 |    80.77 |   93.75 |
ℹ all files                |  99.90 |    87.01 |   96.49 |
ℹ end of coverage report
"""

NODE_NO_COVERAGE = """
ℹ tests 12
ℹ pass 12
ℹ fail 0
"""


class TestNode(unittest.TestCase):

    def test_reads_counts_and_percentages(self):
        m = em.parse_node(NODE_REAL)
        self.assertEqual(m["tests"], 249)
        self.assertAlmostEqual(m["lines"], 99.90)
        self.assertAlmostEqual(m["branches"], 87.01)
        self.assertAlmostEqual(m["functions"], 96.49)

    def test_missing_coverage_table_yields_none_not_zero(self):
        m = em.parse_node(NODE_NO_COVERAGE)
        self.assertEqual(m["tests"], 12)
        self.assertIsNone(m["lines"])
        self.assertIsNone(m["branches"])

    def test_a_test_named_like_the_summary_is_not_counted(self):
        # A suite containing `test('tests 5 things', ...)` must not have its
        # name parsed as the count.
        out = "✔ tests 5 things (1ms)\nℹ tests 3\nℹ pass 3\n"
        self.assertEqual(em.parse_node(out)["tests"], 3)

    def test_empty_output_reports_no_tests(self):
        m = em.parse_node("")
        self.assertIsNone(m["tests"])
        self.assertIsNone(m["lines"])


class TestPython(unittest.TestCase):

    COV = json.dumps({"totals": {"percent_covered": 93.5,
                                 "covered_branches": 40, "num_branches": 50}})

    def test_reads_unittest_count_and_coverage(self):
        m = em.parse_python("Ran 14 tests in 0.006s\nOK\n", self.COV)
        self.assertEqual(m["tests"], 14)
        self.assertAlmostEqual(m["lines"], 93.5)
        self.assertAlmostEqual(m["branches"], 80.0)

    def test_counts_are_summed_across_suites(self):
        # CI runs each file separately, so one job emits several `Ran N` lines.
        # Taking the last would under-report the denominator badly.
        out = "Ran 5 tests in 0.1s\nOK\nRan 9 tests in 0.2s\nOK\n"
        self.assertEqual(em.parse_python(out, None)["tests"], 14)

    def test_functions_is_none_because_coverage_py_has_no_such_metric(self):
        m = em.parse_python("Ran 1 test in 0.0s\n", self.COV)
        self.assertIsNone(m["functions"])

    def test_no_branch_data_yields_none_not_zero(self):
        cov = json.dumps({"totals": {"percent_covered": 80.0}})
        m = em.parse_python("Ran 1 test in 0.0s\n", cov)
        self.assertAlmostEqual(m["lines"], 80.0)
        self.assertIsNone(m["branches"])

    def test_zero_branches_does_not_divide_by_zero(self):
        cov = json.dumps({"totals": {"percent_covered": 80.0,
                                     "covered_branches": 0, "num_branches": 0}})
        m = em.parse_python("Ran 1 test in 0.0s\n", cov)
        self.assertIsNone(m["branches"])

    def test_malformed_coverage_json_yields_none_not_zero(self):
        m = em.parse_python("Ran 2 tests in 0.0s\n", "{broken")
        self.assertEqual(m["tests"], 2)
        self.assertIsNone(m["lines"])

    def test_no_tests_line_reports_none(self):
        # The harness printed nothing recognisable. That is not "0 tests
        # passed" -- the gate must be able to tell those apart.
        self.assertIsNone(em.parse_python("some unrelated output", None)["tests"])


class TestArgRendering(unittest.TestCase):

    def test_absent_metrics_are_omitted_not_zeroed(self):
        args = em.to_args({"tests": 5, "lines": 90.0,
                           "branches": None, "functions": None})
        self.assertIn("--lines", args)
        self.assertNotIn("--branches", args)
        self.assertNotIn("--functions", args)

    def test_absent_test_count_renders_as_zero_so_the_gate_rejects_it(self):
        # The gate's contract is that tests-run <= 0 means "cannot measure".
        # An unparseable count must land there rather than being omitted.
        args = em.to_args({"tests": None, "lines": 99.0})
        self.assertEqual(args[args.index("--tests-run") + 1], "0")


if __name__ == "__main__":
    unittest.main()
