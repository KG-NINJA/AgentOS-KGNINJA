#!/usr/bin/env python3
"""Structural integrity tests for modular analysis architecture."""

import importlib
import os
import unittest


class ModularIntegrityTests(unittest.TestCase):
    def test_modules_import(self):
        for name in [
            "evolution_eval.analyze",
            "evolution_eval.stats_core",
            "evolution_eval.null_simulation",
            "evolution_eval.sensitivity",
            "evolution_eval.reproducibility",
        ]:
            mod = importlib.import_module(name)
            self.assertIsNotNone(mod)

    def test_analyze_has_no_statistical_formulas(self):
        path = os.path.join("/home/user/kg-autonomous", "evolution_eval", "analyze.py")
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # analyze.py should orchestrate only, not embed formulas.
        forbidden = ["math.asin", "math.sqrt", "math.erf", "def theoretical_two_proportion_power", "def wilson_interval("]
        for token in forbidden:
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
