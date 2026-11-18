"""Tests for safe execution mode and serial-failure interlock."""

from __future__ import annotations

import unittest

from app.engine.scenarios import TestCase, TestResult, TestStatus, build_safe_suite
from app.engine.test_engine import TestEngine


class TestSuiteModeTests(unittest.TestCase):
    def test_safe_suite_contains_only_non_disruptive_scenarios(self):
        self.assertEqual([case.test_id for case in build_safe_suite()], ["T01", "T02", "T06"])

    def test_serial_open_failure_skips_all_later_scenarios(self):
        later_called = []

        def passing(ctx, tc):
            return TestResult(tc.test_id, tc.name, TestStatus.PASS, "ok")

        def failing_serial(ctx, tc):
            return TestResult(tc.test_id, tc.name, TestStatus.FAIL, "COM open failed")

        def should_not_run(ctx, tc):
            later_called.append(True)
            return TestResult(tc.test_id, tc.name, TestStatus.PASS, "unexpected")

        engine = TestEngine()
        engine.suite = [
            TestCase("T01", "tools", "", passing),
            TestCase("T02", "serial", "", failing_serial),
            TestCase("T03", "flash", "", should_not_run),
        ]
        results = engine.run(object())

        self.assertEqual(results[1].status, TestStatus.FAIL)
        self.assertEqual(results[2].status, TestStatus.SKIP)
        self.assertFalse(later_called)


if __name__ == "__main__":
    unittest.main()
