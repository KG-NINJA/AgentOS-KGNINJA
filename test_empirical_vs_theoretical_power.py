#!/usr/bin/env python3
"""Empirical-vs-theoretical power calibration tests."""

import unittest

from evolution_eval.sensitivity import run_sensitivity_sweep
from evolution_eval.analyze import build_analysis_result


class EmpiricalTheoreticalPowerTests(unittest.TestCase):
    def test_large_n_power_gap_small(self):
        out = run_sensitivity_sweep(
            sample_sizes=[500],
            baseline_rates=[0.50],
            kernel_rates=[0.65],
            injection_rates=[0.5],
            n_simulations=350,
            seed=1234,
            build_analysis_result=build_analysis_result,
        )
        row = out["results"][0]
        self.assertIsInstance(row["power"], float)
        self.assertIsInstance(row["theoretical_power"], float)
        self.assertLess(row["power_gap"], 0.12)


if __name__ == "__main__":
    unittest.main()
