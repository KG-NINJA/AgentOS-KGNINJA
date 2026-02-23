#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

if np is not None:
    from phase_transition import analyze_model


@unittest.skipIf(np is None, "numpy/scipy/matplotlib are required")
class PhaseTransitionTests(unittest.TestCase):
    def test_continuous_curve(self):
        tau = np.linspace(0.1, 1.4, 13)
        s_vals = 1.0 / (1.0 + np.exp(8.0 * (tau - 0.8)))
        c_vals = np.zeros_like(tau)
        out = analyze_model("m1", tau, s_vals, c_vals, threshold_drop=0.15, n_bootstrap=200)
        self.assertEqual(out["transition_type"], "continuous")
        self.assertIsNotNone(out["tau_c_first_derivative"])

    def test_discontinuous_curve(self):
        tau = np.array([0.2, 0.4, 0.6, 0.8, 1.0, 1.2], dtype=float)
        s_vals = np.array([0.96, 0.95, 0.94, 0.58, 0.57, 0.56], dtype=float)
        c_vals = np.zeros_like(tau)
        out = analyze_model("m2", tau, s_vals, c_vals, threshold_drop=0.15, n_bootstrap=200)
        self.assertEqual(out["transition_type"], "discontinuous")

    def test_flat_curve(self):
        tau = np.array([0.2, 0.6, 1.0, 1.4], dtype=float)
        s_vals = np.array([0.7, 0.7, 0.7, 0.7], dtype=float)
        c_vals = np.zeros_like(tau)
        out = analyze_model("m3", tau, s_vals, c_vals, threshold_drop=0.15, n_bootstrap=200)
        self.assertEqual(out["transition_type"], "none")


if __name__ == "__main__":
    unittest.main()
