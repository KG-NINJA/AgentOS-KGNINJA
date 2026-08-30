from __future__ import annotations

import unittest
from decimal import Decimal

from factory.swarm.baselines import CandidatePrediction, compare_baselines
from factory.swarm.paper import (
    PaperPortfolio,
    PaperRiskPolicy,
    PaperSignal,
    PaperTradeEngine,
    RealExecutionBoundary,
    ResearchReadiness,
    paper_trading_gate,
)


class BaselinesPaperTests(unittest.TestCase):
    def test_matched_baseline_comparison_detects_positive_swarm_advantage(self) -> None:
        rows = []
        for index in range(40):
            case = f"case-{index:03d}"
            outcome = "UP" if index % 2 == 0 else "DOWN"
            wrong = "DOWN" if outcome == "UP" else "UP"
            rows.extend(
                [
                    CandidatePrediction(
                        case, "luna-001", wrong, 0.9, 0.02, "cluster-a", outcome,
                        0.1, inference_cost_usd=0.01, inference_tokens=100,
                    ),
                    CandidatePrediction(
                        case, "luna-002", outcome, 0.8, 0.02, "cluster-b", outcome,
                        0.9, inference_cost_usd=0.01, inference_tokens=100,
                    ),
                    CandidatePrediction(
                        case, "luna-003", outcome, 0.7, 0.01, "cluster-c", outcome,
                        0.8, inference_cost_usd=0.01, inference_tokens=100,
                    ),
                ]
            )
        report = compare_baselines(rows)
        metrics = report["method_metrics"]
        self.assertEqual(metrics["stigmergic_swarm"].hit_rate, 1.0)
        single_advantage = next(
            item
            for item in report["paired_brier_advantage"]
            if item.baseline == "single_luna"
        )
        self.assertTrue(single_advantage.statistically_positive)
        self.assertGreater(single_advantage.ci95_low, 0)
        self.assertTrue(single_advantage.statistically_positive_after_cost)
        self.assertGreater(single_advantage.cost_adjusted_ci95_low, 0)
        self.assertGreater(metrics["stigmergic_swarm"].useful_signal_per_dollar, 0)

    def test_paper_gate_and_deterministic_risk_math(self) -> None:
        blocked = paper_trading_gate(
            ResearchReadiness(10, False, False, 0, 0, None)
        )
        self.assertFalse(blocked.allowed)
        gate = paper_trading_gate(
            ResearchReadiness(1_500, True, True, 0.05, 0.01, 0.05)
        )
        self.assertTrue(gate.allowed)
        portfolio = PaperPortfolio(initial_cash=Decimal("10000"))
        policy = PaperRiskPolicy(allowed_subjects=frozenset({"NVDAc"}))
        engine = PaperTradeEngine(portfolio, policy, gate)
        fill = engine.process(
            PaperSignal(
                artifact_id="prediction-001",
                subject="NVDAc",
                direction="UP",
                confidence=Decimal("0.75"),
                observed_price=Decimal("100"),
                requested_slippage_bps=Decimal("10"),
            )
        )
        self.assertEqual(fill.side, "BUY")
        self.assertGreater(fill.quantity, 0)
        self.assertLessEqual(fill.notional, Decimal("100.01"))
        with self.assertRaises(ValueError):
            engine.process(
                PaperSignal(
                    artifact_id="prediction-001",
                    subject="NVDAc",
                    direction="UP",
                    confidence=Decimal("0.75"),
                    observed_price=Decimal("100"),
                    requested_slippage_bps=Decimal("10"),
                )
            )
        with self.assertRaises(ValueError):
            engine.process(
                PaperSignal(
                    artifact_id="prediction-002",
                    subject="NVDAc",
                    direction="UP",
                    confidence=Decimal("0.9"),
                    observed_price=Decimal("100"),
                    requested_slippage_bps=Decimal("51"),
                )
            )

    def test_real_execution_boundary_is_hard_disabled(self) -> None:
        boundary = RealExecutionBoundary()
        self.assertFalse(boundary.enabled)
        with self.assertRaises(RuntimeError):
            boundary.submit({"side": "BUY"})


if __name__ == "__main__":
    unittest.main()
