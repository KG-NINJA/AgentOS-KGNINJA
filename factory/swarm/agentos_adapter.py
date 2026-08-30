"""Compatibility bridge for AgentOS2 goal ecology and artifact feedback."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .fitness import (
    EXISTING_SIGNAL_DELTAS,
    GoalFitnessVector,
    apply_existing_signal,
    ecology_recommendation,
)
from .schema import Artifact, ArtifactStatus, ArtifactType
from .store import ArtifactStore


@dataclass(frozen=True)
class FeedbackResult:
    goal_id: str
    event_name: str
    artifact_id: str
    existing_signal: float
    recommendation: str


class GoalEcologyAdapter:
    """Append AgentOS2 feedback without taking ownership of goal selection.

    The adapter preserves the exact original deltas and clamp range.  Mutation
    and extinction remain recommendations, because the public AgentOS2 core
    currently exposes no goal_queue mutation API.
    """

    def __init__(self, store: ArtifactStore):
        self.store = store

    def record_event(
        self,
        *,
        goal_id: str,
        event_name: str,
        subject: str,
        notes: str,
        source_ref: str,
    ) -> FeedbackResult:
        if event_name not in EXISTING_SIGNAL_DELTAS:
            raise ValueError(f"unsupported AgentOS2 event: {event_name}")
        previous_artifact = self.store.last_goal_artifact(goal_id)
        is_critic = event_name.startswith("critic_")
        status = {
            "artifact_generated": ArtifactStatus.ACTIVE.value,
            "critic_pass": ArtifactStatus.VALIDATED.value,
            "critic_fail": ArtifactStatus.FAILED.value,
        }[event_name]
        worker_id = "agentos2-critic" if is_critic else "agentos2-builder"
        artifact = Artifact.create(
            worker_id=worker_id,
            artifact_type=(
                ArtifactType.CRITIQUE.value if is_critic else ArtifactType.VALIDATION.value
            ),
            subject=subject,
            hypothesis=f"AgentOS2 goal {goal_id} emitted {event_name}",
            evidence=[
                {
                    "kind": "agentos2_runtime_event",
                    "statement": notes,
                    "external_text": False,
                }
            ],
            source_refs=[source_ref],
            confidence=1.0,
            time_horizon="immediate",
            falsification_condition="A later validation artifact contradicts this runtime event.",
            parent_artifacts=[previous_artifact] if previous_artifact else [],
            derived_from=[],
            status=status,
            metadata={"goal_id": goal_id, "agentos2_event": event_name},
        )
        self.store.append_artifact(artifact)
        current = self.store.current_existing_signal(goal_id)
        resulting = apply_existing_signal(current, event_name)
        self.store.append_goal_fitness_event(
            goal_id=goal_id,
            event_name=event_name,
            delta=EXISTING_SIGNAL_DELTAS[event_name],
            resulting_existing_signal=resulting,
            artifact_id=artifact.artifact_id,
        )
        event_count = self.store.goal_event_count(goal_id)
        return FeedbackResult(
            goal_id=goal_id,
            event_name=event_name,
            artifact_id=artifact.artifact_id,
            existing_signal=resulting,
            recommendation=ecology_recommendation(resulting, event_count),
        )

    def record_extended_fitness(
        self,
        *,
        goal_id: str,
        vector: GoalFitnessVector,
        artifact_id: str | None = None,
    ) -> str:
        """Persist the extended vector alongside, never instead of, legacy fitness."""

        current = self.store.current_existing_signal(goal_id)
        if vector.existing_artifact_signal != current:
            raise ValueError(
                "extended fitness must include the current legacy artifact signal "
                f"({current})"
            )
        payload = {
            **asdict(vector),
            "total": vector.total,
            "extended_component": vector.extended_component,
            "reliability": vector.reliability,
            "axes": asdict(vector.axes),
        }
        return self.store.append_goal_fitness_snapshot(
            goal_id=goal_id,
            fitness_payload=payload,
            artifact_id=artifact_id,
        )
