import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "evolution_eval"))

import transition_matrix as tm


class TransitionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.baseline_rows = [
            {"status": "FAIL", "repair_attempted": False, "repair_success": False, "injection_applied": True},
            {"status": "FAIL", "repair_attempted": True, "repair_success": False, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": False, "repair_success": False, "injection_applied": False},
        ]
        self.kernel_rows = [
            {"status": "FAIL", "repair_attempted": True, "repair_success": False, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": True, "repair_success": True, "injection_applied": True},
            {"status": "SUCCESS", "repair_attempted": False, "repair_success": False, "injection_applied": False},
        ]

    def test_build_and_probabilities(self):
        m = tm.build_matrix(self.kernel_rows)
        p = tm.compute_probabilities(m)
        self.assertIn("S0->S1", p)
        self.assertIn("ci95_lower", p["S0->S1"])
        self.assertLessEqual(p["S0->S1"]["ci95_lower"], p["S0->S1"]["probability"])

    def test_effect_sizes_and_lift(self):
        bp = tm.compute_probabilities(tm.build_matrix(self.baseline_rows))
        kp = tm.compute_probabilities(tm.build_matrix(self.kernel_rows))
        eff = tm.compute_effect_sizes(bp, kp)
        self.assertIn("S1->S2", eff)
        self.assertIn("cohens_h_signed", eff["S1->S2"])

        lift = tm.compute_causal_repair_lift(self.kernel_rows)
        self.assertIn("causal_repair_lift", lift)

    def test_odds_ratio(self):
        out = tm.odds_ratio_with_ci(self.kernel_rows)
        self.assertIn("odds_ratio", out)
        self.assertIn("fisher_exact_p_value", out)
        self.assertGreaterEqual(out["fisher_exact_p_value"], 0.0)
        self.assertLessEqual(out["fisher_exact_p_value"], 1.0)


if __name__ == "__main__":
    unittest.main()
