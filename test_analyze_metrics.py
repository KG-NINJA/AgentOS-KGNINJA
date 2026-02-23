import importlib.util
import os
import unittest


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ROOT = os.path.dirname(os.path.abspath(__file__))
ANALYZE_PATH = os.path.join(ROOT, "evolution_eval", "analyze.py")
TM_PATH = os.path.join(ROOT, "evolution_eval", "transition_matrix.py")

analyze = load_module(ANALYZE_PATH, "analyze_mod")
tm = load_module(TM_PATH, "tm_mod")


class AnalyzeMetricsTests(unittest.TestCase):
    def test_wilson_interval(self):
        ci = analyze.wilson_interval(50, 100)
        self.assertLess(ci["lower"], 0.5)
        self.assertGreater(ci["upper"], 0.5)
        self.assertGreater(ci["upper"] - ci["lower"], 0.15)
        self.assertLess(ci["upper"] - ci["lower"], 0.25)

        ci_zero = analyze.wilson_interval(0, 10)
        self.assertEqual(ci_zero["lower"], 0.0)
        self.assertGreater(ci_zero["upper"], 0.0)

    def test_two_prop(self):
        out = analyze.two_proportion_z_test(50, 100, 30, 100)
        self.assertLess(out["p_value"], 0.05)
        self.assertGreater(out["z_stat"], 0.0)

    def test_repair_metrics(self):
        baseline_rows = []
        kernel_rows = []

        # 20 kernel rows, 10 injected, 6 injected failures, 5 attempts, 4 successes
        for i in range(20):
            injected = i < 10
            fail = i < 6
            repair_attempted = i < 5
            repair_success = i < 4
            kernel_rows.append(
                {
                    "status": "FAIL" if fail else "SUCCESS",
                    "injection_applied": injected,
                    "repair_attempted": repair_attempted,
                    "repair_success": repair_success,
                }
            )

        m = analyze.compute_repair_metrics(baseline_rows, kernel_rows)
        self.assertEqual(m["total_injected_runs"], 10)
        self.assertEqual(m["repair_attempts"], 5)
        self.assertEqual(m["repair_successes"], 4)
        self.assertEqual(m["total_injected_failures"], 6)
        self.assertAlmostEqual(m["repair_attempt_rate"], 0.5)
        self.assertAlmostEqual(m["repair_success_rate"], 0.2)
        self.assertAlmostEqual(m["precision"], 0.8)
        self.assertAlmostEqual(m["recall"], 4 / 6)

    def test_build_analysis_result(self):
        baseline_rows = []
        kernel_rows = []
        for i in range(20):
            baseline_rows.append(
                {
                    "status": "FAIL" if i < 8 else "SUCCESS",
                    "reason_code": "X" if i < 8 else None,
                    "hard_block": False,
                    "injection_applied": i < 10,
                    "injection_mode": "dependency_error" if i < 10 else None,
                    "repair_attempted": False,
                    "repair_success": False,
                }
            )
            kernel_rows.append(
                {
                    "status": "FAIL" if i < 4 else "SUCCESS",
                    "reason_code": "X" if i < 4 else None,
                    "hard_block": False,
                    "injection_applied": i < 10,
                    "injection_mode": "dependency_error" if i < 10 else None,
                    "repair_attempted": i < 5,
                    "repair_success": i < 4,
                }
            )

        r = analyze.build_analysis_result(baseline_rows, kernel_rows)
        self.assertGreater(r["improvement"]["absolute_improvement"], 0)
        self.assertGreater(r["improvement"]["improvement_injected_absolute"], 0)
        self.assertGreaterEqual(r["confidence_intervals"]["baseline_failure_rate"]["lower"], 0)
        self.assertLessEqual(r["confidence_intervals"]["baseline_failure_rate"]["upper"], 1)
        p = r["statistical_tests"]["injected_two_proportion_z_test"]["p_value"]
        self.assertGreaterEqual(p, 0)
        self.assertLessEqual(p, 1)

    def test_transition_module(self):
        rows = [
            {"status": "FAIL", "repair_attempted": True, "repair_success": False},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True},
            {"status": "FAIL", "repair_attempted": False, "repair_success": False},
        ]
        matrix = tm.build_matrix(rows)
        probs = tm.compute_probabilities(matrix)
        self.assertIn("S1->S2", probs)
        self.assertIn("ci95_lower", probs["S1->S2"])


if __name__ == "__main__":
    unittest.main()
