#!/usr/bin/env python3
"""Deterministic tests for sensitivity sweep layer in evolution_eval.analyze."""

import json
import unittest

from evolution_eval.analyze import run_sensitivity_sweep


class SensitivitySweepTests(unittest.TestCase):
    def test_deterministic_output_same_seed(self):
        a = run_sensitivity_sweep(
            sample_sizes=[20, 50],
            baseline_rates=[0.5],
            kernel_rates=[0.5, 0.6],
            injection_rates=[0.4],
            n_simulations=120,
            seed=123,
        )
        b = run_sensitivity_sweep(
            sample_sizes=[20, 50],
            baseline_rates=[0.5],
            kernel_rates=[0.5, 0.6],
            injection_rates=[0.4],
            n_simulations=120,
            seed=123,
        )
        self.assertEqual(a, b)

    def test_fpr_close_to_alpha_under_null(self):
        out = run_sensitivity_sweep(
            sample_sizes=[100],
            baseline_rates=[0.5],
            kernel_rates=[0.5],
            injection_rates=[0.5],
            n_simulations=500,
            seed=7,
        )
        row = out["results"][0]
        # Monte Carlo tolerance around alpha=0.05.
        self.assertGreaterEqual(row["fpr"], 0.02)
        self.assertLessEqual(row["fpr"], 0.10)

    def test_power_increases_with_effect_size(self):
        out = run_sensitivity_sweep(
            sample_sizes=[100],
            baseline_rates=[0.5],
            kernel_rates=[0.5, 0.65],
            injection_rates=[0.5],
            n_simulations=350,
            seed=21,
        )
        by_kernel = {r["kernel_rate"]: r for r in out["results"]}
        self.assertGreaterEqual(by_kernel[0.65]["power"], by_kernel[0.5]["power"])

    def test_cf_variance_decreases_with_sample_size(self):
        out = run_sensitivity_sweep(
            sample_sizes=[20, 200],
            baseline_rates=[0.55],
            kernel_rates=[0.45],
            injection_rates=[0.5],
            n_simulations=300,
            seed=99,
        )
        by_n = {r["sample_size"]: r for r in out["results"]}
        self.assertGreaterEqual(by_n[20]["cf_index_var"], by_n[200]["cf_index_var"])

    def test_probability_and_metric_ranges(self):
        out = run_sensitivity_sweep(
            sample_sizes=[30],
            baseline_rates=[0.4],
            kernel_rates=[0.6],
            injection_rates=[0.3],
            n_simulations=100,
            seed=55,
        )
        row = out["results"][0]
        self.assertGreaterEqual(row["fpr"], 0.0)
        self.assertLessEqual(row["fpr"], 1.0)
        self.assertGreaterEqual(row["power"], 0.0)
        self.assertLessEqual(row["power"], 1.0)
        self.assertGreaterEqual(row["cf_index_var"], 0.0)
        self.assertGreaterEqual(row["or_mean"], 0.0)

    def test_stable_json_serialization(self):
        out = run_sensitivity_sweep(
            sample_sizes=[20],
            baseline_rates=[0.5],
            kernel_rates=[0.6],
            injection_rates=[0.5],
            n_simulations=20,
            seed=5,
        )
        s1 = json.dumps(out, sort_keys=True, separators=(",", ":"))
        s2 = json.dumps(out, sort_keys=True, separators=(",", ":"))
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
