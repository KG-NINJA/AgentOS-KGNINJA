"""Matched-condition baselines for the primary swarm research question."""

from __future__ import annotations

import hashlib
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .schema import Direction


@dataclass(frozen=True)
class CandidatePrediction:
    case_id: str
    worker_id: str
    expected_direction: str
    confidence: float
    expected_magnitude: float
    evidence_cluster_id: str
    realized_direction: str
    swarm_score: float = 0.0
    inference_cost_usd: float = 0.0
    inference_tokens: int = 0
    usefulness_score: float | None = None

    def validate(self) -> None:
        Direction(self.expected_direction)
        Direction(self.realized_direction)
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.inference_cost_usd < 0 or self.inference_tokens < 0:
            raise ValueError("inference cost and tokens cannot be negative")
        if self.usefulness_score is not None and not 0 <= self.usefulness_score <= 1:
            raise ValueError("usefulness_score must be between 0 and 1")


@dataclass(frozen=True)
class MethodMetrics:
    cases: int
    hit_rate: float
    brier_score_one_vs_selected: float
    inference_cost_usd: float
    inference_tokens: int
    useful_signal_per_dollar: float | None
    useful_signal_per_token: float | None
    cost_adjusted_brier: float


@dataclass(frozen=True)
class PairedAdvantage:
    baseline: str
    mean_brier_improvement: float
    ci95_low: float
    ci95_high: float
    statistically_positive: bool
    mean_cost_adjusted_improvement: float
    cost_adjusted_ci95_low: float
    cost_adjusted_ci95_high: float
    statistically_positive_after_cost: bool


def _representatives(items: list[CandidatePrediction]) -> list[CandidatePrediction]:
    by_cluster: dict[str, list[CandidatePrediction]] = defaultdict(list)
    for item in items:
        by_cluster[item.evidence_cluster_id].append(item)
    return [
        max(values, key=lambda item: (item.confidence, item.worker_id))
        for values in by_cluster.values()
    ]


def _select(method: str, items: list[CandidatePrediction]) -> CandidatePrediction:
    if method == "single_luna":
        return min(items, key=lambda item: item.worker_id)
    if method == "best_of_n_independent":
        return max(
            _representatives(items),
            key=lambda item: (item.confidence, item.swarm_score, item.worker_id),
        )
    if method == "stigmergic_swarm":
        return max(
            items,
            key=lambda item: (item.swarm_score, item.confidence, item.worker_id),
        )
    if method == "majority_vote":
        representatives = _representatives(items)
        counts = Counter(item.expected_direction for item in representatives)
        confidence_sums = defaultdict(float)
        for item in representatives:
            confidence_sums[item.expected_direction] += item.confidence
        direction = max(
            counts,
            key=lambda value: (counts[value], confidence_sums[value], value),
        )
        return max(
            (item for item in representatives if item.expected_direction == direction),
            key=lambda item: (item.confidence, item.worker_id),
        )
    if method == "random_baseline":
        digest = hashlib.sha256(items[0].case_id.encode()).digest()
        direction = (Direction.UP.value, Direction.DOWN.value, Direction.FLAT.value)[
            digest[0] % 3
        ]
        template = min(items, key=lambda item: item.worker_id)
        return CandidatePrediction(
            case_id=template.case_id,
            worker_id="random-baseline",
            expected_direction=direction,
            confidence=1 / 3,
            expected_magnitude=0.0,
            evidence_cluster_id="random",
            realized_direction=template.realized_direction,
            swarm_score=0.0,
            inference_cost_usd=0.0,
            inference_tokens=0,
            usefulness_score=None,
        )
    raise ValueError(f"unknown comparison method: {method}")


def _loss(item: CandidatePrediction) -> float:
    correct = item.expected_direction == item.realized_direction
    return (item.confidence - (1.0 if correct else 0.0)) ** 2


def _metrics(
    items: list[CandidatePrediction],
    *,
    inference_cost_usd: float,
    inference_tokens: int,
    cost_penalty_per_usd: float,
) -> MethodMetrics:
    useful = sum(
        item.usefulness_score
        if item.usefulness_score is not None
        else float(item.expected_direction == item.realized_direction)
        for item in items
    )
    brier = statistics.fmean(_loss(item) for item in items)
    return MethodMetrics(
        cases=len(items),
        hit_rate=statistics.fmean(
            item.expected_direction == item.realized_direction for item in items
        ),
        brier_score_one_vs_selected=brier,
        inference_cost_usd=inference_cost_usd,
        inference_tokens=inference_tokens,
        useful_signal_per_dollar=(useful / inference_cost_usd if inference_cost_usd else None),
        useful_signal_per_token=(useful / inference_tokens if inference_tokens else None),
        cost_adjusted_brier=(
            brier + cost_penalty_per_usd * (inference_cost_usd / len(items))
        ),
    )


def _paired_bootstrap(
    swarm: list[CandidatePrediction],
    baseline: list[CandidatePrediction],
    *,
    swarm_costs: list[float],
    baseline_costs: list[float],
    cost_penalty_per_usd: float,
    seed: int,
    draws: int = 2_000,
) -> tuple[float, float, float, float, float, float]:
    if (
        len(swarm) != len(baseline)
        or len(swarm) != len(swarm_costs)
        or len(swarm) != len(baseline_costs)
        or not swarm
    ):
        raise ValueError("paired bootstrap requires matched non-empty cases")
    improvements = [_loss(base) - _loss(candidate) for candidate, base in zip(swarm, baseline)]
    adjusted = [
        improvement
        + cost_penalty_per_usd * (baseline_cost - swarm_cost)
        for improvement, baseline_cost, swarm_cost in zip(
            improvements, baseline_costs, swarm_costs
        )
    ]
    mean = statistics.fmean(improvements)
    adjusted_mean = statistics.fmean(adjusted)
    rng = random.Random(seed)
    samples: list[float] = []
    adjusted_samples: list[float] = []
    for _ in range(draws):
        indices = [rng.randrange(len(improvements)) for _ in improvements]
        samples.append(statistics.fmean(improvements[index] for index in indices))
        adjusted_samples.append(statistics.fmean(adjusted[index] for index in indices))
    samples.sort()
    adjusted_samples.sort()
    low = samples[int(0.025 * (draws - 1))]
    high = samples[int(0.975 * (draws - 1))]
    adjusted_low = adjusted_samples[int(0.025 * (draws - 1))]
    adjusted_high = adjusted_samples[int(0.975 * (draws - 1))]
    return mean, low, high, adjusted_mean, adjusted_low, adjusted_high


def compare_baselines(
    candidates: Iterable[CandidatePrediction],
    *,
    seed: int = 402,
    cost_penalty_per_usd: float = 1.0,
) -> dict[str, object]:
    if cost_penalty_per_usd < 0:
        raise ValueError("cost_penalty_per_usd cannot be negative")
    by_case: dict[str, list[CandidatePrediction]] = defaultdict(list)
    for item in candidates:
        item.validate()
        by_case[item.case_id].append(item)
    if not by_case:
        raise ValueError("comparison dataset is empty")
    methods = (
        "single_luna",
        "best_of_n_independent",
        "majority_vote",
        "stigmergic_swarm",
        "random_baseline",
    )
    selected: dict[str, list[CandidatePrediction]] = {method: [] for method in methods}
    costs = {method: 0.0 for method in methods}
    tokens = {method: 0 for method in methods}
    case_costs: dict[str, list[float]] = {method: [] for method in methods}
    for case_id in sorted(by_case):
        rows = by_case[case_id]
        realized = {row.realized_direction for row in rows}
        if len(realized) != 1:
            raise ValueError(f"case {case_id} has inconsistent realized outcomes")
        for method in methods:
            chosen = _select(method, rows)
            selected[method].append(chosen)
            if method == "single_luna":
                case_cost = chosen.inference_cost_usd
                costs[method] += case_cost
                tokens[method] += chosen.inference_tokens
            elif method != "random_baseline":
                # Independent-N, majority, and swarm all pay for the same N
                # candidate inferences. Swarm coordination cost should be
                # represented as additional candidate cost in the dataset.
                case_cost = sum(item.inference_cost_usd for item in rows)
                costs[method] += case_cost
                tokens[method] += sum(item.inference_tokens for item in rows)
            else:
                case_cost = 0.0
            case_costs[method].append(case_cost)
    metrics = {
        method: _metrics(
            rows,
            inference_cost_usd=costs[method],
            inference_tokens=tokens[method],
            cost_penalty_per_usd=cost_penalty_per_usd,
        )
        for method, rows in selected.items()
    }
    advantages: list[PairedAdvantage] = []
    swarm = selected["stigmergic_swarm"]
    for index, method in enumerate(methods):
        if method == "stigmergic_swarm":
            continue
        mean, low, high, adjusted_mean, adjusted_low, adjusted_high = _paired_bootstrap(
            swarm,
            selected[method],
            swarm_costs=case_costs["stigmergic_swarm"],
            baseline_costs=case_costs[method],
            cost_penalty_per_usd=cost_penalty_per_usd,
            seed=seed + index,
        )
        advantages.append(
            PairedAdvantage(
                baseline=method,
                mean_brier_improvement=mean,
                ci95_low=low,
                ci95_high=high,
                statistically_positive=low > 0,
                mean_cost_adjusted_improvement=adjusted_mean,
                cost_adjusted_ci95_low=adjusted_low,
                cost_adjusted_ci95_high=adjusted_high,
                statistically_positive_after_cost=adjusted_low > 0,
            )
        )
    return {
        "research_question": (
            "Does an artifact-sharing Luna swarm outperform matched independent or single Luna "
            "after inference cost?"
        ),
        "method_metrics": metrics,
        "paired_brier_advantage": advantages,
        "independence_rule": "one representative per evidence cluster for independent-N and majority vote",
        "selection_rule": "all selectors are fixed before realized outcomes; no hindsight best-of-N",
        "cost_adjustment": {
            "penalty_per_usd_in_brier_units": cost_penalty_per_usd,
            "rule": "cost-adjusted loss = one-vs-selected Brier + penalty * USD per case",
        },
    }
