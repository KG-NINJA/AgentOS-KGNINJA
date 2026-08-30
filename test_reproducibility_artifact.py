#!/usr/bin/env python3
"""Reproducibility artifact determinism tests."""

import json
import tempfile
import unittest
from pathlib import Path

from evolution_eval.analyze import build_analysis_result
from evolution_eval.reproducibility import build_repro_report
from evolution_eval.sensitivity import run_sensitivity_sweep


ROOT = Path(__file__).resolve().parent


class ReproducibilityArtifactTests(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows):
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")

    def test_same_seed_identical_repro_report(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.jsonl"
            k = Path(td) / "k.jsonl"
            rows = [{"status": "SUCCESS", "reason_code": None, "repair_attempted": False, "repair_success": False}]
            self._write_jsonl(b, rows)
            self._write_jsonl(k, rows)

            modules = [
                str(ROOT / "evolution_eval" / "analyze.py"),
                str(ROOT / "evolution_eval" / "stats_core.py"),
            ]
            cfg = {"a": 1, "b": True}
            r1 = build_repro_report(config=cfg, input_files=[str(b), str(k)], module_files=modules, seed=42, sweep_payload=None)
            r2 = build_repro_report(config=cfg, input_files=[str(b), str(k)], module_files=modules, seed=42, sweep_payload=None)
            self.assertEqual(r1, r2)

    def test_different_seed_changes_sweep_hash(self):
        s1 = run_sensitivity_sweep(
            sample_sizes=[20],
            baseline_rates=[0.5],
            kernel_rates=[0.6],
            injection_rates=[0.5],
            n_simulations=50,
            seed=1,
            build_analysis_result=build_analysis_result,
        )
        s2 = run_sensitivity_sweep(
            sample_sizes=[20],
            baseline_rates=[0.5],
            kernel_rates=[0.6],
            injection_rates=[0.5],
            n_simulations=50,
            seed=2,
            build_analysis_result=build_analysis_result,
        )

        modules = [str(ROOT / "evolution_eval" / "analyze.py")]
        cfg = {"x": 1}
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "rows.jsonl"
            self._write_jsonl(f, [{"status": "SUCCESS", "reason_code": None, "repair_attempted": False, "repair_success": False}])
            r1 = build_repro_report(config=cfg, input_files=[str(f)], module_files=modules, seed=1, sweep_payload=s1)
            r2 = build_repro_report(config=cfg, input_files=[str(f)], module_files=modules, seed=2, sweep_payload=s2)
            self.assertNotEqual(r1["sweep_hash"], r2["sweep_hash"])


if __name__ == "__main__":
    unittest.main()
