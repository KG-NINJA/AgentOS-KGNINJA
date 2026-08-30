"""Fitness extension that preserves AgentOS2's original artifact signal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


EXISTING_SIGNAL_DELTAS: dict[str, float] = {
    "artifact_generated": 1.0,
    "critic_pass": 2.0,
    "critic_fail": -1.0,
}
EXISTING_SIGNAL_MIN = -5.0
EXISTING_SIGNAL_MAX = 10.0


def clamp_existing_signal(value: float) -> float:
    return min(EXISTING_SIGNAL_MAX, max(EXISTING_SIGNAL_MIN, float(value)))


def apply_existing_signal(current: float, event_name: str) -> float:
    try:
        delta = EXISTING_SIGNAL_DELTAS[event_name]
    except KeyError as exc:
        raise ValueError(f"unknown existing artifact signal: {event_name}") from exc
    return clamp_existing_signal(float(current) + delta)


def _unit(value: float, name: str) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class FitnessAxes:
    """Independent objectives; demand never overwrites truth."""

    truth: float
    usefulness: float
    demand: float

    def __post_init__(self) -> None:
        _unit(self.truth, "truth")
        _unit(self.usefulness, "usefulness")
        _unit(self.demand, "demand")


@dataclass(frozen=True)
class GoalFitnessVector:
    existing_artifact_signal: float
    prediction_accuracy: float
    calibration_score: float
    novelty: float
    evidence_quality: float
    downstream_usefulness: float
    economic_value: float
    inference_cost_penalty: float
    evaluated_samples: int

    def __post_init__(self) -> None:
        if self.existing_artifact_signal != clamp_existing_signal(
            self.existing_artifact_signal
        ):
            raise ValueError("existing_artifact_signal must stay within [-5, 10]")
        for name in (
            "prediction_accuracy",
            "calibration_score",
            "novelty",
            "evidence_quality",
            "downstream_usefulness",
            "economic_value",
            "inference_cost_penalty",
        ):
            _unit(getattr(self, name), name)
        if self.evaluated_samples < 0:
            raise ValueError("evaluated_samples cannot be negative")

    @property
    def reliability(self) -> float:
        # A single lucky artifact receives only 1/11 of the extended weight.
        return self.evaluated_samples / (self.evaluated_samples + 10.0)

    @property
    def extended_component(self) -> float:
        positive = (
            2.0 * self.prediction_accuracy
            + 2.0 * self.calibration_score
            + 1.0 * self.novelty
            + 1.5 * self.evidence_quality
            + 1.5 * self.downstream_usefulness
            + 1.0 * self.economic_value
        )
        penalty = 2.0 * self.inference_cost_penalty
        return self.reliability * (positive - penalty)

    @property
    def total(self) -> float:
        # The original signal is included byte-for-byte, not renormalized.
        return self.existing_artifact_signal + self.extended_component

    @property
    def axes(self) -> FitnessAxes:
        truth = (self.prediction_accuracy + self.calibration_score + self.evidence_quality) / 3
        usefulness = (self.novelty + self.downstream_usefulness) / 2
        return FitnessAxes(truth=truth, usefulness=usefulness, demand=self.economic_value)


@dataclass(frozen=True)
class StrategyFitness:
    score: float
    horizon_scores: dict[str, float]
    horizon_coverage: float
    sample_count: int


def score_strategy_over_horizons(
    horizon_values: Mapping[str, tuple[float, int]],
) -> StrategyFitness:
    """Score 24h/7d/30d while preventing one horizon from dominating."""

    supported = ("24h", "7d", "30d")
    scores: dict[str, float] = {}
    total_samples = 0
    for horizon in supported:
        if horizon not in horizon_values:
            continue
        raw_score, samples = horizon_values[horizon]
        raw_score = _unit(raw_score, f"{horizon} score")
        if samples < 0:
            raise ValueError("horizon sample count cannot be negative")
        # Shrink each horizon independently toward neutral until it has evidence.
        reliability = samples / (samples + 20.0)
        scores[horizon] = 0.5 + reliability * (raw_score - 0.5)
        total_samples += samples
    coverage = len(scores) / len(supported)
    if not scores:
        return StrategyFitness(0.0, {}, 0.0, 0)
    balanced = sum(scores.values()) / len(scores)
    return StrategyFitness(
        score=balanced * coverage,
        horizon_scores=scores,
        horizon_coverage=coverage,
        sample_count=total_samples,
    )


def ecology_recommendation(existing_signal: float, event_count: int) -> str:
    """Recommend selection/mutation/extinction without deleting evidence."""

    score = clamp_existing_signal(existing_signal)
    if event_count >= 5 and score <= EXISTING_SIGNAL_MIN:
        return "EXTINCTION_CANDIDATE_RETAIN_ARTIFACTS"
    if event_count >= 3 and score < 0:
        return "MUTATE"
    if score >= 3:
        return "SELECT"
    return "OBSERVE"
