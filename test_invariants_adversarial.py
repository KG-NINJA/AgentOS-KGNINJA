import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(ROOT, "evolution_eval")
sys.path.insert(0, EVAL_DIR)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


analyze = load_module("analyze_mod_adv", os.path.join(EVAL_DIR, "analyze.py"))
tm = load_module("tm_mod_adv", os.path.join(EVAL_DIR, "transition_matrix.py"))


class TransitionInvariantTests(unittest.TestCase):
    def test_transition_probability_invariants(self):
        rows = [
            {"status": "FAIL", "repair_attempted": False, "repair_success": False, "injection_applied": True},
            {"status": "FAIL", "repair_attempted": True, "repair_success": False, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True, "injection_applied": False},
        ]
        matrix = tm.build_matrix(rows)
        probs = tm.compute_probabilities(matrix)

        # Count consistency: each transition count must be <= denominator.
        for t, info in probs.items():
            self.assertLessEqual(info["count"], info["denominator"], msg=t)

        # CI bounds must stay within [0,1].
        for t, info in probs.items():
            self.assertGreaterEqual(info["ci95_lower"], 0.0, msg=t)
            self.assertLessEqual(info["ci95_upper"], 1.0, msg=t)
            self.assertLessEqual(info["ci95_lower"], info["probability"], msg=t)
            self.assertLessEqual(info["probability"], info["ci95_upper"], msg=t)

        # Row-probability invariants for true Markov branches.
        # S0 final-state partition uses S0->S2 and S0->S3.
        if probs["S0->S2"]["denominator"] > 0:
            s0_sum = probs["S0->S2"]["probability"] + probs["S0->S3"]["probability"]
            self.assertAlmostEqual(s0_sum, 1.0, places=10)

        # S1 branches: attempted repairs end in S2 or S3.
        if probs["S1->S2"]["denominator"] > 0:
            s1_sum = probs["S1->S2"]["probability"] + probs["S1->S3"]["probability"]
            self.assertAlmostEqual(s1_sum, 1.0, places=10)

    def test_inconsistent_rows_raise_value_error(self):
        bad_rows = [
            {"status": "SUCCESS", "repair_attempted": False, "repair_success": True},
        ]
        with self.assertRaises(ValueError):
            tm.build_matrix(bad_rows)

        bad_rows2 = [
            {"status": "UNKNOWN", "repair_attempted": False, "repair_success": False},
        ]
        with self.assertRaises(ValueError):
            tm.build_matrix(bad_rows2)


class ExtremeSmallSampleTests(unittest.TestCase):
    def test_n1_wilson(self):
        ci1 = analyze.wilson_interval(1, 1)
        ci0 = analyze.wilson_interval(0, 1)
        self.assertGreaterEqual(ci1["lower"], 0.0)
        self.assertLessEqual(ci1["upper"], 1.0)
        self.assertGreaterEqual(ci0["lower"], 0.0)
        self.assertLessEqual(ci0["upper"], 1.0)

    def test_all_success_all_failure(self):
        all_success = [{"status": "SUCCESS", "reason_code": None, "hard_block": False, "injection_applied": True, "repair_attempted": True, "repair_success": True} for _ in range(5)]
        all_failure = [{"status": "FAIL", "reason_code": "DEPENDENCY_ERROR", "hard_block": False, "injection_applied": True, "repair_attempted": True, "repair_success": False} for _ in range(5)]

        r_success = analyze.build_analysis_result(all_success, all_success)
        r_failure = analyze.build_analysis_result(all_failure, all_failure)

        self.assertEqual(r_success["baseline"]["failure_rate"], 0.0)
        self.assertEqual(r_failure["baseline"]["failure_rate"], 1.0)


class NumericalCoherenceTests(unittest.TestCase):
    def test_cf_index_sign_matches_signed_h(self):
        baseline = []
        kernel = []

        # baseline lower S2 recovery among injected DEPENDENCY_ERROR
        for i in range(20):
            baseline.append(
                {
                    "status": "SUCCESS" if i < 4 else "FAIL",
                    "reason_code": "DEPENDENCY_ERROR",
                    "hard_block": False,
                    "injection_applied": True,
                    "repair_attempted": True,
                    "repair_success": i < 4,
                }
            )

        # kernel higher S2 recovery
        for i in range(20):
            kernel.append(
                {
                    "status": "SUCCESS" if i < 10 else "FAIL",
                    "reason_code": "DEPENDENCY_ERROR",
                    "hard_block": False,
                    "injection_applied": True,
                    "repair_attempted": True,
                    "repair_success": i < 10,
                }
            )

        out = analyze.build_analysis_result(baseline, kernel, causal_mode="injected_only")
        cf = out["counterfactual_analysis"]["cf_index"]
        h = out["counterfactual_analysis"]["effect_size"]["cohens_h_signed"]

        # Sign coherence between difference in probabilities and signed effect size.
        self.assertGreater(cf, 0)
        self.assertGreater(h, 0)

    def test_or_gt_one_when_cf_positive(self):
        baseline = []
        kernel = []

        for i in range(30):
            baseline.append(
                {
                    "status": "SUCCESS" if i < 6 else "FAIL",
                    "reason_code": "DEPENDENCY_ERROR",
                    "hard_block": False,
                    "injection_applied": True,
                    "repair_attempted": i < 15,
                    "repair_success": i < 6,
                }
            )
            kernel.append(
                {
                    "status": "SUCCESS" if i < 15 else "FAIL",
                    "reason_code": "DEPENDENCY_ERROR",
                    "hard_block": False,
                    "injection_applied": True,
                    "repair_attempted": i < 20,
                    "repair_success": i < 15,
                }
            )

        out = analyze.build_analysis_result(baseline, kernel, causal_mode="injected_only")
        cf = out["counterfactual_analysis"]["cf_index"]
        or_kernel = out["transition_analysis"]["odds_ratio_kernel"]["odds_ratio"]

        self.assertGreater(cf, 0)
        self.assertGreater(or_kernel, 1.0)


if __name__ == "__main__":
    unittest.main()
