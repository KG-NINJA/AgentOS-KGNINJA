"""Artifact-mediated orchestration for a homogeneous Luna worker population."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .anti_herding import select_observations
from .schema import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    Direction,
    PredictionFields,
)
from .specialization import SpecializationDetector
from .store import ArtifactStore, WorkerState


COMMON_CAPABILITIES = (
    "market_analysis",
    "technology_research",
    "company_fundamentals",
    "news_event_analysis",
    "anomaly_detection",
    "contrarian_analysis",
    "verification",
    "historical_comparison",
    "evidence_criticism",
    "synthesis",
)


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
    model: str
    virtual_budget: float
    common_capabilities: tuple[str, ...]
    subject: str
    time_horizon: str
    observed_artifacts: tuple[Artifact, ...]
    successful_behavior_hint: str | None


class LunaWorkerClient(Protocol):
    def generate(self, context: WorkerContext) -> Artifact:
        """Generate one artifact from environment observations only."""


@dataclass(frozen=True)
class SwarmRoundResult:
    requested_workers: int
    stored_artifact_ids: tuple[str, ...]
    failed_worker_ids: tuple[str, ...]


class SwarmOrchestrator:
    def __init__(
        self,
        store: ArtifactStore,
        client: LunaWorkerClient,
        *,
        initial_worker_count: int = 50,
        max_parallel: int = 10,
        observation_limit: int = 12,
        minority_fraction: float = 0.2,
    ):
        self.store = store
        self.client = client
        self.initial_worker_count = initial_worker_count
        self.max_parallel = max_parallel
        self.observation_limit = observation_limit
        self.minority_fraction = minority_fraction

    def bootstrap(self) -> list[str]:
        return self.store.register_workers(
            count=self.initial_worker_count,
            model="gpt-5.6-luna",
            capabilities=COMMON_CAPABILITIES,
            initial_budget=100.0,
        )

    def run_round(
        self,
        *,
        subject: str,
        time_horizon: str,
        worker_limit: int | None = None,
    ) -> SwarmRoundResult:
        all_workers = self.store.workers()
        workers = [worker for worker in all_workers if worker.virtual_budget > 0]
        workers.sort(key=lambda worker: (-worker.virtual_budget, worker.worker_id))
        if worker_limit is not None:
            workers = workers[:worker_limit]
        environment = self.store.list_artifacts(subject=subject, limit=10_000)
        observations = select_observations(
            environment,
            limit=min(self.observation_limit, max(1, len(environment))),
            minority_fraction=self.minority_fraction,
        ) if environment else []

        stored: list[str] = []
        failed: list[str] = []
        registered_worker_ids = {worker.worker_id for worker in all_workers}
        with ThreadPoolExecutor(max_workers=max(1, self.max_parallel)) as executor:
            futures = {
                executor.submit(
                    self.client.generate,
                    self._context(worker, subject, time_horizon, observations),
                ): worker
                for worker in workers
            }
            for future in as_completed(futures):
                worker = futures[future]
                try:
                    artifact = future.result()
                    self._validate_worker_output(worker, subject, artifact)
                    self.store.append_artifact(artifact)
                    self._reward_downstream_reuse(
                        artifact,
                        registered_worker_ids=registered_worker_ids,
                    )
                    stored.append(artifact.artifact_id)
                except Exception as exc:  # failure itself becomes observable evidence
                    failed.append(worker.worker_id)
                    failure = Artifact.create(
                        worker_id=worker.worker_id,
                        artifact_type=ArtifactType.CRITIQUE.value,
                        subject=subject,
                        hypothesis="Worker generation failed before producing a valid artifact.",
                        evidence=[
                            {
                                "kind": "worker_failure",
                                "exception_type": type(exc).__name__,
                            }
                        ],
                        source_refs=[f"agentos2://swarm/worker/{worker.worker_id}"],
                        confidence=1.0,
                        time_horizon=time_horizon,
                        falsification_condition="A later retry produces a schema-valid artifact.",
                        parent_artifacts=[],
                        derived_from=[],
                        status=ArtifactStatus.FAILED.value,
                    )
                    self.store.append_artifact(failure)
                    stored.append(failure.artifact_id)
                    self.store.append_budget_event(
                        worker_id=worker.worker_id,
                        delta=-1.0,
                        reason="invalid_or_failed_generation",
                        related_artifact_id=failure.artifact_id,
                    )
        SpecializationDetector(self.store).detect_and_persist_all()
        return SwarmRoundResult(
            requested_workers=len(workers),
            stored_artifact_ids=tuple(sorted(stored)),
            failed_worker_ids=tuple(sorted(failed)),
        )

    def _reward_downstream_reuse(
        self,
        artifact: Artifact,
        *,
        registered_worker_ids: set[str],
    ) -> None:
        for parent_id in sorted(
            set(artifact.parent_artifacts + artifact.derived_from)
        ):
            parent = self.store.get_artifact(parent_id)
            if (
                parent is None
                or parent.worker_id == artifact.worker_id
                or parent.worker_id not in registered_worker_ids
            ):
                continue
            self.store.append_budget_event(
                worker_id=parent.worker_id,
                delta=0.25,
                reason="downstream_artifact_reuse",
                related_artifact_id=parent.artifact_id,
            )

    @staticmethod
    def _context(
        worker: WorkerState,
        subject: str,
        time_horizon: str,
        observations: list[Artifact],
    ) -> WorkerContext:
        # Rotation prevents every worker from seeing an identical leading item.
        if observations:
            offset = int(hashlib.sha256(worker.worker_id.encode()).hexdigest()[:8], 16)
            offset %= len(observations)
            rotated = observations[offset:] + observations[:offset]
        else:
            rotated = []
        return WorkerContext(
            worker_id=worker.worker_id,
            model=worker.model,
            virtual_budget=worker.virtual_budget,
            common_capabilities=worker.capabilities,
            subject=subject,
            time_horizon=time_horizon,
            observed_artifacts=tuple(rotated),
            successful_behavior_hint=worker.behavioral_pattern,
        )

    @staticmethod
    def _validate_worker_output(
        worker: WorkerState,
        subject: str,
        artifact: Artifact,
    ) -> None:
        artifact.validate()
        if artifact.worker_id != worker.worker_id:
            raise ValueError("worker output worker_id mismatch")
        if artifact.subject != subject:
            raise ValueError("worker output subject mismatch")


class DeterministicLunaTestDouble:
    """Offline test double.  It is not a market model and never runs by default."""

    def generate(self, context: WorkerContext) -> Artifact:
        digest = hashlib.sha256(
            f"{context.worker_id}|{context.subject}|{context.time_horizon}".encode()
        ).hexdigest()
        direction = (Direction.UP.value, Direction.DOWN.value, Direction.FLAT.value)[
            int(digest[:2], 16) % 3
        ]
        now = datetime.now(timezone.utc)
        evaluation_at = now + timedelta(hours=24)
        parents = [item.artifact_id for item in context.observed_artifacts[:2]]
        source_refs = [f"test-double://{context.subject}/{context.worker_id}"]
        return Artifact.create(
            worker_id=context.worker_id,
            artifact_type=ArtifactType.PREDICTION.value,
            subject=context.subject,
            hypothesis="Deterministic test-double output; not a live market claim.",
            evidence=[
                {
                    "kind": "test_double",
                    "observed_artifact_ids": parents,
                    "behavior_hint": context.successful_behavior_hint,
                }
            ],
            source_refs=source_refs,
            confidence=0.5,
            time_horizon=context.time_horizon,
            falsification_condition="The deterministic fixture outcome differs.",
            parent_artifacts=parents,
            derived_from=[],
            prediction=PredictionFields(
                asset_or_subject=context.subject,
                expected_direction=direction,
                expected_magnitude=0.01 if direction != Direction.FLAT.value else 0.0,
                evaluation_at=evaluation_at.isoformat().replace("+00:00", "Z"),
            ),
            metadata={"test_double": True, "inference_cost_usd": 0.0, "inference_tokens": 0},
            created_at=now.isoformat().replace("+00:00", "Z"),
        )
