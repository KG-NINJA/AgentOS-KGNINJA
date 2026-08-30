from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.swarm.anti_herding import (
    cluster_by_evidence,
    independent_cluster_vote,
    select_observations,
)
from factory.swarm.orchestration import (
    COMMON_CAPABILITIES,
    DeterministicLunaTestDouble,
    SwarmOrchestrator,
)
from factory.swarm.schema import Artifact, ArtifactType, PredictionFields
from factory.swarm.specialization import SpecializationDetector
from factory.swarm.store import ArtifactStore


def prediction(index: int, direction: str, source: str) -> Artifact:
    return Artifact.create(
        artifact_id=f"pred-{index:03d}",
        created_at=f"2026-01-01T00:{index:02d}:00Z",
        worker_id=f"luna-{index + 1:03d}",
        artifact_type=ArtifactType.PREDICTION.value,
        subject="NVDAc",
        hypothesis=f"Fixture direction {direction}",
        evidence=[{"kind": "fixture", "source": source}],
        source_refs=[source],
        confidence=0.6 + index / 1000,
        time_horizon="24h",
        falsification_condition="Fixture outcome differs.",
        prediction=PredictionFields(
            asset_or_subject="NVDAc",
            expected_direction=direction,
            expected_magnitude=0.01 if direction != "FLAT" else 0,
            evaluation_at="2026-01-02T00:00:00Z",
        ),
    )


class ReuseClient:
    def generate(self, context):
        parents = [item.artifact_id for item in context.observed_artifacts[:1]]
        return Artifact.create(
            artifact_id=f"reuse-{context.worker_id}",
            worker_id=context.worker_id,
            artifact_type=ArtifactType.EVIDENCE.value,
            subject=context.subject,
            hypothesis="Fixture reuses an observed artifact.",
            evidence=[{"kind": "reuse_fixture", "parents": parents}],
            source_refs=[f"fixture://reuse/{context.worker_id}"],
            confidence=0.6,
            time_horizon=context.time_horizon,
            falsification_condition="The downstream fixture is rejected.",
            parent_artifacts=parents,
        )


class OrchestrationAntiHerdingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(Path(self.temp.name) / "swarm.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_shared_source_is_one_vote_and_minority_survives(self) -> None:
        artifacts = [
            prediction(index, "UP", "https://source.test/shared?copy=" + str(index))
            for index in range(8)
        ]
        # Query variants normalize to one primary source chain.
        artifacts.extend(
            [
                prediction(8, "DOWN", "https://minority-a.test/report"),
                prediction(9, "DOWN", "https://minority-b.test/report"),
            ]
        )
        clusters = cluster_by_evidence(artifacts)
        self.assertEqual(len(clusters), 3)
        votes = independent_cluster_vote(artifacts)
        self.assertEqual(votes["UP"], 1)
        self.assertEqual(votes["DOWN"], 2)
        selected = select_observations(artifacts, limit=5, minority_fraction=0.4)
        selected_directions = [item.prediction.expected_direction for item in selected]
        self.assertGreaterEqual(selected_directions.count("UP"), 1)
        self.assertGreaterEqual(selected_directions.count("DOWN"), 1)

    def test_bootstrap_has_50_homogeneous_workers_and_artifact_only_round(self) -> None:
        orchestrator = SwarmOrchestrator(
            self.store,
            DeterministicLunaTestDouble(),
            initial_worker_count=50,
            max_parallel=10,
        )
        worker_ids = orchestrator.bootstrap()
        self.assertEqual(len(worker_ids), 50)
        workers = self.store.workers()
        self.assertEqual(
            {frozenset(worker.capabilities) for worker in workers},
            {frozenset(COMMON_CAPABILITIES)},
        )
        self.assertEqual({worker.behavioral_pattern for worker in workers}, {None})
        result = orchestrator.run_round(subject="NVDAc", time_horizon="24h")
        self.assertEqual(result.requested_workers, 50)
        self.assertFalse(result.failed_worker_ids)
        self.assertEqual(len(result.stored_artifact_ids), 50)
        self.assertEqual(self.store.summary()["artifact_count"], 50)

    def test_specialization_is_observed_only_after_reused_history(self) -> None:
        self.store.register_workers(count=1, capabilities=COMMON_CAPABILITIES)
        parent_ids = []
        for index in range(5):
            parent = Artifact.create(
                artifact_id=f"counter-{index}",
                created_at=f"2026-01-01T00:0{index}:00Z",
                worker_id="luna-001",
                artifact_type=ArtifactType.COUNTER_EVIDENCE.value,
                subject="NVDAc",
                hypothesis="Contrarian fixture",
                evidence=[{"kind": "fixture"}],
                source_refs=[f"fixture://counter/{index}"],
                confidence=0.7,
                time_horizon="24h",
                falsification_condition="Validated by outcome.",
            )
            self.store.append_artifact(parent)
            parent_ids.append(parent.artifact_id)
            child = Artifact.create(
                artifact_id=f"validation-{index}",
                created_at=f"2026-01-01T01:0{index}:00Z",
                worker_id="swarm-evaluator",
                artifact_type=ArtifactType.VALIDATION.value,
                subject="NVDAc",
                hypothesis="Downstream reuse",
                evidence=[{"kind": "fixture"}],
                source_refs=[f"fixture://validation/{index}"],
                confidence=1,
                time_horizon="24h",
                falsification_condition="Validation is corrected.",
                parent_artifacts=[parent.artifact_id],
            )
            self.store.append_artifact(child)
        observation = SpecializationDetector(self.store).detect("luna-001")
        self.assertTrue(observation.reusable)
        self.assertEqual(observation.pattern, "contrarian_analysis")

    def test_cross_worker_reuse_increases_parent_worker_budget(self) -> None:
        orchestrator = SwarmOrchestrator(
            self.store,
            ReuseClient(),
            initial_worker_count=2,
            max_parallel=2,
        )
        orchestrator.bootstrap()
        parent = Artifact.create(
            artifact_id="reuse-parent-001",
            worker_id="luna-001",
            artifact_type=ArtifactType.EVIDENCE.value,
            subject="NVDAc",
            hypothesis="Reusable parent fixture.",
            evidence=[{"kind": "fixture"}],
            source_refs=["fixture://reuse/parent"],
            confidence=0.7,
            time_horizon="24h",
            falsification_condition="A child validation rejects it.",
        )
        self.store.append_artifact(parent)
        orchestrator.run_round(subject="NVDAc", time_horizon="24h")
        budgets = {item.worker_id: item.virtual_budget for item in self.store.workers()}
        self.assertEqual(budgets["luna-001"], 100.25)
        self.assertEqual(budgets["luna-002"], 100.0)


if __name__ == "__main__":
    unittest.main()
