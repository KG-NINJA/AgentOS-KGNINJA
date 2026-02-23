#!/usr/bin/env python3
"""Unit tests for Python kernel orchestration and strict repair trigger behavior."""

import json
import tempfile
import unittest
from pathlib import Path

from evolution_eval.kernel_orchestrator import run_kernel_pipeline


class KernelOrchestratorTests(unittest.TestCase):
    def _executor_from_rows(self, rows):
        queue = [dict(row) for row in rows]

        def _executor():
            if not queue:
                raise AssertionError("executor called more times than expected")
            return queue.pop(0)

        return _executor

    def test_repair_triggers_for_dependency_error_and_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            artifact_path = str(Path(td) / "artifact.json")
            executor = self._executor_from_rows(
                [
                    {"exit_code": 1, "decision_status": "ok", "post_gate_status": "ok"},
                    {"exit_code": 0, "decision_status": "ok", "post_gate_status": "ok"},
                ]
            )

            def classifier(artifact, _decision_limit_reason_code):
                return ("FAIL", "DEPENDENCY_ERROR", True) if artifact["exit_code"] != 0 else ("SUCCESS", None, False)

            repair_calls = {"n": 0}

            def repairer():
                repair_calls["n"] += 1
                return True

            result = run_kernel_pipeline(
                {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"},
                artifact_path,
                executor=executor,
                classifier=classifier,
                repairer=repairer,
            )

            self.assertEqual(result["initial_status"], "FAIL")
            self.assertEqual(result["final_status"], "SUCCESS")
            self.assertTrue(result["repair_attempted"])
            self.assertTrue(result["repair_success"])
            self.assertEqual(repair_calls["n"], 1)

            artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
            self.assertTrue(artifact["repair_attempted"])
            self.assertTrue(artifact["repair_success"])
            self.assertEqual(artifact["status"], "SUCCESS")
            self.assertIsNone(artifact["reason_code"])

    def test_repair_not_triggered_for_other_reason_code(self):
        with tempfile.TemporaryDirectory() as td:
            artifact_path = str(Path(td) / "artifact.json")
            executor = self._executor_from_rows(
                [{"exit_code": 1, "decision_status": "ok", "post_gate_status": "ok"}]
            )

            def classifier(_artifact, _decision_limit_reason_code):
                return "FAIL", "OTHER_ERROR", True

            called = {"repair": False}

            def repairer():
                called["repair"] = True
                return True

            result = run_kernel_pipeline(
                {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"},
                artifact_path,
                executor=executor,
                classifier=classifier,
                repairer=repairer,
            )

            self.assertFalse(result["repair_attempted"])
            self.assertFalse(result["repair_success"])
            self.assertFalse(called["repair"])
            self.assertEqual(result["final_status"], "FAIL")
            self.assertEqual(result["reason_code"], "OTHER_ERROR")

    def test_invalid_status_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            artifact_path = str(Path(td) / "artifact.json")
            executor = self._executor_from_rows(
                [{"exit_code": 1, "decision_status": "ok", "post_gate_status": "ok"}]
            )

            def classifier(_artifact, _decision_limit_reason_code):
                return "BROKEN", "DEPENDENCY_ERROR", True

            with self.assertRaises(ValueError):
                run_kernel_pipeline(
                    {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"},
                    artifact_path,
                    executor=executor,
                    classifier=classifier,
                    repairer=lambda: True,
                )

    def test_deterministic_repeated_runs_identical_output(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"}

            def classifier(_artifact, _decision_limit_reason_code):
                return "SUCCESS", None, False

            path1 = str(Path(td) / "artifact1.json")
            path2 = str(Path(td) / "artifact2.json")

            res1 = run_kernel_pipeline(
                config,
                path1,
                executor=self._executor_from_rows(
                    [{"exit_code": 0, "decision_status": "ok", "post_gate_status": "ok"}]
                ),
                classifier=classifier,
                repairer=lambda: False,
            )
            res2 = run_kernel_pipeline(
                config,
                path2,
                executor=self._executor_from_rows(
                    [{"exit_code": 0, "decision_status": "ok", "post_gate_status": "ok"}]
                ),
                classifier=classifier,
                repairer=lambda: False,
            )

            self.assertEqual(res1, res2)
            self.assertEqual(
                Path(path1).read_text(encoding="utf-8"),
                Path(path2).read_text(encoding="utf-8"),
            )

    def test_reason_code_preserved_when_failure_persists(self):
        with tempfile.TemporaryDirectory() as td:
            artifact_path = str(Path(td) / "artifact.json")
            executor = self._executor_from_rows(
                [{"exit_code": 1, "decision_status": "ok", "post_gate_status": "ok"}]
            )

            def classifier(_artifact, _decision_limit_reason_code):
                return "FAIL", "QUALITY_GATE_FAIL", True

            result = run_kernel_pipeline(
                {"decision_limit_reason_code": "DECISION_LIMIT_EXCLUDED"},
                artifact_path,
                executor=executor,
                classifier=classifier,
                repairer=lambda: False,
            )

            self.assertEqual(result["final_status"], "FAIL")
            self.assertEqual(result["reason_code"], "QUALITY_GATE_FAIL")
            artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
            self.assertEqual(artifact["reason_code"], "QUALITY_GATE_FAIL")


if __name__ == "__main__":
    unittest.main()
