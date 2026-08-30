"""Maturity-aware automatic evaluation and deterministic research metrics."""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass
from typing import Protocol

from .anti_herding import cluster_by_evidence
from .schema import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Direction,
    EvaluationRecord,
    parse_timestamp,
)
from .store import ArtifactStore


@dataclass(frozen=True)
class RealizedObservation:
    realized_result: float
    realized_direction: str
    observed_at: str
    source_refs: list[str]
    usefulness_score: float | None = None

    def validate(self) -> None:
        Direction(self.realized_direction)
        parse_timestamp(self.observed_at, "observed_at")
        if not math.isfinite(float(self.realized_result)):
            raise ValueError("realized_result must be finite")
        if not self.source_refs:
            raise ValueError("realized observation requires source_refs")
        if self.usefulness_score is not None and not 0 <= self.usefulness_score <= 1:
            raise ValueError("usefulness_score must be between 0 and 1")


class OutcomeResolver(Protocol):
    def resolve(self, prediction: Artifact) -> RealizedObservation | None:
        """Return a timestamped result, or None when settlement is unavailable."""


def _signed_expected_magnitude(artifact: Artifact) -> float:
    assert artifact.prediction is not None
    magnitude = abs(float(artifact.prediction.expected_magnitude))
    if artifact.prediction.expected_direction == Direction.DOWN.value:
        return -magnitude
    if artifact.prediction.expected_direction in {
        Direction.FLAT.value,
        Direction.NO_SIGNAL.value,
    }:
        return 0.0
    return magnitude


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else None


class EvaluationEngine:
    VERSION = "swarm-eval-v1"

    def __init__(self, store: ArtifactStore):
        self.store = store

    def evaluate_due(
        self,
        *,
        now: str,
        resolver: OutcomeResolver,
    ) -> list[EvaluationRecord]:
        now_dt = parse_timestamp(now, "now")
        due = self.store.due_predictions(now)
        all_predictions = self.store.list_artifacts(
            artifact_type=ArtifactType.PREDICTION.value, limit=10_000
        )
        cluster_size: dict[str, int] = {}
        for cluster in cluster_by_evidence(all_predictions):
            for artifact_id in cluster.artifact_ids:
                cluster_size[artifact_id] = len(cluster.artifact_ids)

        records: list[EvaluationRecord] = []
        for prediction in due:
            observation = resolver.resolve(prediction)
            if observation is None:
                continue
            observation.validate()
            assert prediction.prediction is not None
            evaluation_at = parse_timestamp(
                prediction.prediction.evaluation_at, "evaluation_at"
            )
            observed_at = parse_timestamp(observation.observed_at, "observed_at")
            if observed_at < evaluation_at or observed_at > now_dt:
                self._record_temporal_contamination(
                    prediction=prediction,
                    observation=observation,
                    now=now,
                    reason=(
                        "outcome_before_prediction_maturity"
                        if observed_at < evaluation_at
                        else "outcome_timestamp_after_evaluation_run"
                    ),
                )
                continue
            predicted = prediction.prediction.expected_direction
            correct = predicted == observation.realized_direction
            # Binary one-vs-selected-class Brier; the metric name is explicit in reports.
            brier = (prediction.confidence - (1.0 if correct else 0.0)) ** 2
            uniqueness = 1.0 / max(1, cluster_size.get(prediction.artifact_id, 1))
            usefulness = (
                observation.usefulness_score
                if observation.usefulness_score is not None
                else (1.0 if correct else 0.0) * uniqueness
            )
            cost = max(0.0, float(prediction.metadata.get("inference_cost_usd", 0.0)))
            token_count = max(0, int(prediction.metadata.get("inference_tokens", 0)))
            record = EvaluationRecord(
                evaluation_id=f"eval_{uuid.uuid4().hex}",
                prediction_artifact_id=prediction.artifact_id,
                realized_result=float(observation.realized_result),
                realized_direction=observation.realized_direction,
                prediction_error=_signed_expected_magnitude(prediction)
                - float(observation.realized_result),
                brier_score=max(0.0, min(1.0, brier)),
                calibration_score=max(0.0, min(1.0, 1.0 - brier)),
                usefulness_score=float(usefulness),
                uniqueness_score=uniqueness,
                cost=cost,
                token_count=token_count,
                evaluated_at=observation.observed_at,
                source_refs=list(observation.source_refs),
                evaluator_version=self.VERSION,
            )
            record.validate()
            self.store.append_evaluation(record)
            outcome = Artifact.create(
                worker_id="swarm-evaluator",
                artifact_type=ArtifactType.OUTCOME.value,
                subject=prediction.subject,
                hypothesis="Observed outcome for a matured immutable prediction.",
                evidence=[
                    {
                        "kind": "realized_outcome",
                        "realized_result": observation.realized_result,
                        "realized_direction": observation.realized_direction,
                        "prediction_error": record.prediction_error,
                        "brier_score_one_vs_selected": record.brier_score,
                    }
                ],
                source_refs=list(observation.source_refs),
                confidence=1.0,
                time_horizon=prediction.time_horizon,
                falsification_condition="A provenance-audited correction invalidates the settlement observation.",
                parent_artifacts=[prediction.artifact_id],
                derived_from=[],
                status=ArtifactStatus.EVALUATED.value,
                metadata={"evaluation_id": record.evaluation_id},
                created_at=observation.observed_at,
            )
            self.store.append_artifact(outcome)
            self._apply_worker_budget(prediction, record, correct=correct)
            records.append(record)
        return records

    def _record_temporal_contamination(
        self,
        *,
        prediction: Artifact,
        observation: RealizedObservation,
        now: str,
        reason: str,
    ) -> None:
        assert prediction.prediction is not None
        anomaly = Artifact.create(
            worker_id="swarm-evaluator",
            artifact_type=ArtifactType.ANOMALY.value,
            subject=prediction.subject,
            hypothesis="Outcome was rejected to prevent retrospective or future-data leakage.",
            evidence=[
                {
                    "kind": "temporal_contamination",
                    "reason": reason,
                    "evaluation_at": prediction.prediction.evaluation_at,
                    "outcome_observed_at": observation.observed_at,
                    "evaluation_run_at": now,
                }
            ],
            source_refs=list(observation.source_refs),
            confidence=1.0,
            time_horizon=prediction.time_horizon,
            falsification_condition=(
                "A provenance-audited observation timestamp within the valid settlement window "
                "is supplied."
            ),
            parent_artifacts=[prediction.artifact_id],
            derived_from=[],
            status=ArtifactStatus.FAILED.value,
            metadata={"leakage_guard": True, "reason": reason},
            created_at=now,
        )
        self.store.append_artifact(anomaly)

    def _apply_worker_budget(
        self,
        prediction: Artifact,
        record: EvaluationRecord,
        *,
        correct: bool,
    ) -> None:
        known_workers = {worker.worker_id for worker in self.store.workers()}
        if prediction.worker_id not in known_workers:
            return
        truth_delta = 2.0 if correct else -2.0
        calibration_delta = 2.0 * (record.calibration_score - 0.5)
        novelty_delta = record.uniqueness_score
        duplicate_penalty = 1.0 - record.uniqueness_score
        usefulness_delta = record.usefulness_score
        cost_penalty = min(2.0, record.cost / 0.01) if record.cost else 0.0
        delta = (
            truth_delta
            + calibration_delta
            + novelty_delta
            + usefulness_delta
            - duplicate_penalty
            - cost_penalty
        )
        self.store.append_budget_event(
            worker_id=prediction.worker_id,
            delta=delta,
            reason="matured_prediction_evaluation",
            related_artifact_id=prediction.artifact_id,
        )

    def metrics(self) -> dict[str, float | int | None | str]:
        evaluations = self.store.evaluations()
        if not evaluations:
            return {
                "evaluated_predictions": 0,
                "brier_score_one_vs_selected": None,
                "calibration_ece": None,
                "hit_rate": None,
                "information_coefficient": None,
                "false_positive_rate": None,
                "precision": None,
                "recall": None,
                "prediction_uniqueness": None,
                "useful_signal_per_dollar": None,
                "useful_signal_per_token": None,
                "artifact_survival_rate": None,
            }
        predictions = {
            artifact.artifact_id: artifact
            for artifact in self.store.list_artifacts(
                artifact_type=ArtifactType.PREDICTION.value, limit=10_000
            )
        }
        correct: list[bool] = []
        expected_values: list[float] = []
        realized_values: list[float] = []
        for evaluation in evaluations:
            prediction = predictions[evaluation.prediction_artifact_id]
            assert prediction.prediction is not None
            predicted = prediction.prediction.expected_direction
            is_correct = predicted == evaluation.realized_direction
            correct.append(is_correct)
            expected_values.append(_signed_expected_magnitude(prediction))
            realized_values.append(evaluation.realized_result)

        ece = self._expected_calibration_error(evaluations, predictions)
        false_positive_rate, precision, recall = self._macro_directional_metrics(
            evaluations, predictions
        )
        useful_total = sum(item.usefulness_score for item in evaluations)
        cost_total = sum(item.cost for item in evaluations)
        token_total = sum(item.token_count for item in evaluations)
        all_artifacts = self.store.list_artifacts(limit=10_000)
        survived = sum(self.store.reuse_count(item.artifact_id) > 0 for item in all_artifacts)
        return {
            "evaluated_predictions": len(evaluations),
            "brier_score_one_vs_selected": statistics.fmean(
                item.brier_score for item in evaluations
            ),
            "calibration_ece": ece,
            "hit_rate": statistics.fmean(correct),
            "information_coefficient": _pearson(expected_values, realized_values),
            "false_positive_rate": false_positive_rate,
            "precision": precision,
            "recall": recall,
            "prediction_uniqueness": statistics.fmean(
                item.uniqueness_score for item in evaluations
            ),
            "useful_signal_per_dollar": useful_total / cost_total if cost_total else None,
            "useful_signal_per_token": useful_total / token_total if token_total else None,
            "artifact_survival_rate": survived / len(all_artifacts) if all_artifacts else None,
        }

    @staticmethod
    def _macro_directional_metrics(
        evaluations: list[EvaluationRecord],
        predictions: dict[str, Artifact],
    ) -> tuple[float | None, float | None, float | None]:
        false_positive_rates: list[float] = []
        precisions: list[float] = []
        recalls: list[float] = []
        for label in (Direction.UP.value, Direction.DOWN.value):
            tp = fp = tn = fn = 0
            for evaluation in evaluations:
                predicted = predictions[
                    evaluation.prediction_artifact_id
                ].prediction.expected_direction
                actual = evaluation.realized_direction
                tp += int(predicted == label and actual == label)
                fp += int(predicted == label and actual != label)
                tn += int(predicted != label and actual != label)
                fn += int(predicted != label and actual == label)
            if fp + tn:
                false_positive_rates.append(fp / (fp + tn))
            if tp + fp:
                precisions.append(tp / (tp + fp))
            if tp + fn:
                recalls.append(tp / (tp + fn))
        return (
            statistics.fmean(false_positive_rates) if false_positive_rates else None,
            statistics.fmean(precisions) if precisions else None,
            statistics.fmean(recalls) if recalls else None,
        )

    @staticmethod
    def _expected_calibration_error(
        evaluations: list[EvaluationRecord],
        predictions: dict[str, Artifact],
        bins: int = 10,
    ) -> float:
        total = len(evaluations)
        error = 0.0
        for index in range(bins):
            lower = index / bins
            upper = (index + 1) / bins
            bucket = [
                evaluation
                for evaluation in evaluations
                if lower
                <= predictions[evaluation.prediction_artifact_id].confidence
                <= (upper if index == bins - 1 else upper - 1e-15)
            ]
            if not bucket:
                continue
            accuracy = statistics.fmean(
                predictions[item.prediction_artifact_id].prediction.expected_direction
                == item.realized_direction
                for item in bucket
            )
            confidence = statistics.fmean(
                predictions[item.prediction_artifact_id].confidence for item in bucket
            )
            error += len(bucket) / total * abs(accuracy - confidence)
        return error
