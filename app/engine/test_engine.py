"""Sequential test runner."""

from __future__ import annotations

import traceback
from typing import Callable, List, Optional

from app.engine.scenarios import TestCase, TestContext, TestResult, TestStatus, build_default_suite


class TestEngine:
    def __init__(
        self,
        on_result: Optional[Callable[[TestResult], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_finished: Optional[Callable[[List[TestResult]], None]] = None,
    ) -> None:
        self.suite: List[TestCase] = build_default_suite()
        self.on_result = on_result or (lambda r: None)
        self.on_log = on_log or (lambda m: None)
        self.on_finished = on_finished or (lambda rs: None)
        self._abort = False
        self.results: List[TestResult] = []

    def abort(self) -> None:
        self._abort = True

    def run(self, ctx: TestContext) -> List[TestResult]:
        self._abort = False
        self.results = []
        self.on_log("=== Test run started ===")

        for tc in self.suite:
            if self._abort:
                res = TestResult(tc.test_id, tc.name, TestStatus.SKIP, "aborted")
                self.results.append(res)
                self.on_result(res)
                continue
            if not tc.enabled:
                res = TestResult(tc.test_id, tc.name, TestStatus.SKIP, "disabled")
                self.results.append(res)
                self.on_result(res)
                continue

            self.on_log(f"--- {tc.test_id}: {tc.name} ---")
            running = TestResult(tc.test_id, tc.name, TestStatus.RUNNING)
            self.on_result(running)
            try:
                res = tc.run(ctx, tc)
            except Exception as e:
                self.on_log(traceback.format_exc())
                res = TestResult(tc.test_id, tc.name, TestStatus.FAIL, str(e))

            self.results.append(res)
            self.on_result(res)
            self.on_log(f"{tc.test_id} => {res.status.value}: {res.message}")

        self.on_log("=== Test run finished ===")
        self.on_finished(self.results)
        return self.results
