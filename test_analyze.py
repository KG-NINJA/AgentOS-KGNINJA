import importlib.util
import math
import os
import unittest


def load_analyze_module():
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, "evolution_eval", "analyze.py")
    spec = importlib.util.spec_from_file_location("analyze_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


analyze = load_analyze_module()


class TestCohensH(unittest.TestCase):
    def test_cohens_h_zero(self):
        self.assertAlmostEqual(analyze.cohens_h(0.5, 0.5), 0.0, places=12)

    def test_cohens_h_positive(self):
        self.assertGreater(analyze.cohens_h(0.8, 0.2), 0.0)

    def test_interpretation_thresholds(self):
        self.assertEqual(analyze.interpret_cohens_h(0.0), "negligible")
        self.assertEqual(analyze.interpret_cohens_h(0.19), "negligible")
        self.assertEqual(analyze.interpret_cohens_h(0.2), "small")
        self.assertEqual(analyze.interpret_cohens_h(0.49), "small")
        self.assertEqual(analyze.interpret_cohens_h(0.5), "medium")
        self.assertEqual(analyze.interpret_cohens_h(0.79), "medium")
        self.assertEqual(analyze.interpret_cohens_h(0.8), "large")
        self.assertEqual(analyze.interpret_cohens_h(-0.81), "large")


if __name__ == "__main__":
    unittest.main()
