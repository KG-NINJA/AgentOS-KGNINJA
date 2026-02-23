#!/usr/bin/env python3
"""CSV export determinism tests for sweep output."""

import tempfile
import unittest
from pathlib import Path

from evolution_eval.analyze import build_analysis_result
from evolution_eval.sensitivity import export_sweep_to_csv, run_sensitivity_sweep


class CsvDeterminismTests(unittest.TestCase):
    def test_byte_identical_csv_for_same_input(self):
        sweep = run_sensitivity_sweep(
            sample_sizes=[20, 50],
            baseline_rates=[0.4],
            kernel_rates=[0.6],
            injection_rates=[0.5],
            n_simulations=80,
            seed=42,
            build_analysis_result=build_analysis_result,
        )
        with tempfile.TemporaryDirectory() as td:
            p1 = Path(td) / "a.csv"
            p2 = Path(td) / "b.csv"
            export_sweep_to_csv(sweep, str(p1))
            export_sweep_to_csv(sweep, str(p2))
            self.assertEqual(p1.read_bytes(), p2.read_bytes())


if __name__ == "__main__":
    unittest.main()
