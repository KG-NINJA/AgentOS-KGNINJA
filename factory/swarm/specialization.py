"""Detect successful behavioral patterns without assigning fixed personas."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from .schema import ArtifactType
from .store import ArtifactStore


PATTERN_BY_ARTIFACT_TYPE = {
    ArtifactType.PREDICTION.value: "market_analysis",
    ArtifactType.MARKET_OBSERVATION.value: "market_analysis",
    ArtifactType.TECHNOLOGY_SIGNAL.value: "technology_research",
    ArtifactType.COMPANY_SIGNAL.value: "company_fundamentals",
    ArtifactType.ANOMALY.value: "anomaly_detection",
    ArtifactType.COUNTER_EVIDENCE.value: "contrarian_analysis",
    ArtifactType.VALIDATION.value: "verification",
    ArtifactType.CRITIQUE.value: "evidence_criticism",
    ArtifactType.STRATEGY.value: "synthesis",
    ArtifactType.EVIDENCE.value: "news_event_analysis",
    ArtifactType.OUTCOME.value: "historical_comparison",
    ArtifactType.COMMERCIAL_PRODUCT.value: "synthesis",
}


@dataclass(frozen=True)
class SpecializationObservation:
    worker_id: str
    pattern: str | None
    artifact_count: int
    pattern_count: int
    pattern_share: float
    quality_advantage: float
    reusable: bool


class SpecializationDetector:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        min_artifacts: int = 5,
        min_pattern_artifacts: int = 3,
        min_share: float = 0.4,
        min_quality_advantage: float = 0.1,
    ):
        self.store = store
        self.min_artifacts = min_artifacts
        self.min_pattern_artifacts = min_pattern_artifacts
        self.min_share = min_share
        self.min_quality_advantage = min_quality_advantage

    def detect(self, worker_id: str) -> SpecializationObservation:
        artifacts = self.store.list_artifacts(worker_id=worker_id, limit=10_000)
        pattern_scores: dict[str, list[float]] = defaultdict(list)
        for artifact in artifacts:
            pattern = PATTERN_BY_ARTIFACT_TYPE[artifact.artifact_type]
            evaluation = self.store.evaluation_for(artifact.artifact_id)
            if evaluation is not None:
                quality = statistics.fmean(
                    [
                        evaluation.calibration_score,
                        evaluation.usefulness_score,
                        evaluation.uniqueness_score,
                    ]
                )
            else:
                # Reuse is an observed downstream signal, not popularity alone.
                quality = min(1.0, self.store.reuse_count(artifact.artifact_id) / 3.0)
            pattern_scores[pattern].append(quality)
        if not artifacts:
            return SpecializationObservation(worker_id, None, 0, 0, 0.0, 0.0, False)
        top_pattern = max(
            pattern_scores,
            key=lambda pattern: (
                statistics.fmean(pattern_scores[pattern]),
                len(pattern_scores[pattern]),
                pattern,
            ),
        )
        top_values = pattern_scores[top_pattern]
        other_values = [
            score
            for pattern, scores in pattern_scores.items()
            if pattern != top_pattern
            for score in scores
        ]
        top_quality = statistics.fmean(top_values)
        other_quality = statistics.fmean(other_values) if other_values else 0.0
        advantage = top_quality - other_quality
        share = len(top_values) / len(artifacts)
        reusable = (
            len(artifacts) >= self.min_artifacts
            and len(top_values) >= self.min_pattern_artifacts
            and share >= self.min_share
            and advantage >= self.min_quality_advantage
        )
        return SpecializationObservation(
            worker_id=worker_id,
            pattern=top_pattern if reusable else None,
            artifact_count=len(artifacts),
            pattern_count=len(top_values),
            pattern_share=share,
            quality_advantage=advantage,
            reusable=reusable,
        )

    def detect_and_persist_all(self) -> list[SpecializationObservation]:
        observations = [self.detect(worker.worker_id) for worker in self.store.workers()]
        for observation in observations:
            self.store.set_behavioral_pattern(
                observation.worker_id,
                observation.pattern if observation.reusable else None,
            )
        return observations
