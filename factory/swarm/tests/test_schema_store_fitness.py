from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from factory.swarm.agentos_adapter import GoalEcologyAdapter
from factory.swarm.fitness import (
    GoalFitnessVector,
    apply_existing_signal,
    score_strategy_over_horizons,
)
from factory.swarm.schema import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    PredictionFields,
    SchemaError,
)
from factory.swarm.store import ArtifactStore


class SchemaStoreFitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(Path(self.temp.name) / "swarm.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def evidence_artifact(index: int, *, parent: str | None = None) -> Artifact:
        return Artifact.create(
            artifact_id=f"art-evidence-{index:03d}",
            created_at=f"2026-01-01T00:{index:02d}:00Z",
            worker_id=f"luna-{index + 1:03d}",
            artifact_type=ArtifactType.EVIDENCE.value,
            subject="NVDAc",
            hypothesis=f"Independent evidence {index}",
            evidence=[{"kind": "fixture", "index": index}],
            source_refs=[f"https://example.test/source/{index}"],
            confidence=0.6,
            time_horizon="24h",
            falsification_condition="The fixture outcome disagrees.",
            parent_artifacts=[parent] if parent else [],
            derived_from=[],
        )

    def test_prediction_fields_and_secret_guard(self) -> None:
        with self.assertRaises(SchemaError):
            Artifact.create(
                worker_id="luna-001",
                artifact_type=ArtifactType.PREDICTION.value,
                subject="NVDAc",
                hypothesis="Missing prediction fields",
                evidence=[],
                source_refs=["fixture://market"],
                confidence=0.5,
                time_horizon="24h",
                falsification_condition="Outcome differs.",
            )
        with self.assertRaises(SchemaError):
            Artifact.create(
                worker_id="luna-001",
                artifact_type=ArtifactType.EVIDENCE.value,
                subject="NVDAc",
                hypothesis="Credential-bearing provenance URL",
                evidence=[{"kind": "fixture"}],
                source_refs=["https://example.test/data?api_key=do-not-store"],
                confidence=0.5,
                time_horizon="24h",
                falsification_condition="Outcome differs.",
            )
        with self.assertRaises(SchemaError):
            Artifact.create(
                worker_id="luna-001",
                artifact_type=ArtifactType.EVIDENCE.value,
                subject="NVDAc",
                hypothesis="Unsafe payload",
                evidence=[],
                source_refs=["fixture://market"],
                confidence=0.5,
                time_horizon="24h",
                falsification_condition="Outcome differs.",
                metadata={"private_key": "forbidden"},
            )
        with self.assertRaises(SchemaError):
            Artifact.create(
                worker_id="luna-001",
                artifact_type=ArtifactType.EVIDENCE.value,
                subject="NVDAc",
                hypothesis="Missing provenance",
                evidence=[{"kind": "fixture"}],
                source_refs=[],
                confidence=0.5,
                time_horizon="24h",
                falsification_condition="Outcome differs.",
            )

    def test_append_only_lineage_and_checksum(self) -> None:
        parent = self.evidence_artifact(1)
        child = self.evidence_artifact(2, parent=parent.artifact_id)
        self.store.append_artifact(parent)
        self.store.append_artifact(child)
        self.assertEqual(
            self.store.lineage(child.artifact_id)["ancestors"], [parent.artifact_id]
        )
        self.assertEqual(self.store.get_artifact(child.artifact_id), child)
        with sqlite3.connect(self.store.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE artifacts SET status = 'FAILED' WHERE artifact_id = ?",
                    (parent.artifact_id,),
                )

    def test_concurrent_writes_are_complete(self) -> None:
        artifacts = [self.evidence_artifact(index) for index in range(20)]
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(self.store.append_artifact, artifacts))
        self.assertEqual(self.store.summary()["artifact_count"], 20)

    def test_agentos_signal_values_clamp_and_failure_lineage(self) -> None:
        adapter = GoalEcologyAdapter(self.store)
        first = adapter.record_event(
            goal_id="goal-001",
            event_name="artifact_generated",
            subject="project-001",
            notes="builder output",
            source_ref="agentos2://test/builder",
        )
        passed = adapter.record_event(
            goal_id="goal-001",
            event_name="critic_pass",
            subject="project-001",
            notes="critic pass",
            source_ref="agentos2://test/critic/pass",
        )
        failed = adapter.record_event(
            goal_id="goal-001",
            event_name="critic_fail",
            subject="project-001",
            notes="critic fail retained",
            source_ref="agentos2://test/critic/fail",
        )
        self.assertEqual(first.existing_signal, 1)
        self.assertEqual(passed.existing_signal, 3)
        self.assertEqual(failed.existing_signal, 2)
        failure_artifact = self.store.get_artifact(failed.artifact_id)
        self.assertEqual(failure_artifact.status, ArtifactStatus.FAILED.value)
        lineage = self.store.lineage(failed.artifact_id)["ancestors"]
        self.assertEqual(set(lineage), {first.artifact_id, passed.artifact_id})
        score = 0.0
        for _ in range(20):
            score = apply_existing_signal(score, "critic_pass")
        self.assertEqual(score, 10.0)

        vector = GoalFitnessVector(
            existing_artifact_signal=2,
            prediction_accuracy=0.6,
            calibration_score=0.7,
            novelty=0.8,
            evidence_quality=0.9,
            downstream_usefulness=0.5,
            economic_value=0.1,
            inference_cost_penalty=0.2,
            evaluated_samples=20,
        )
        snapshot_id = adapter.record_extended_fitness(
            goal_id="goal-001",
            vector=vector,
            artifact_id=failed.artifact_id,
        )
        snapshot = self.store.latest_goal_fitness("goal-001")
        self.assertEqual(snapshot["snapshot_id"], snapshot_id)
        self.assertEqual(snapshot["fitness"]["existing_artifact_signal"], 2)
        self.assertEqual(snapshot["fitness"]["axes"]["demand"], 0.1)
        self.assertEqual(self.store.summary()["goal_fitness_snapshot_count"], 1)

    def test_extended_fitness_shrinks_lucky_single_and_balances_horizons(self) -> None:
        single = GoalFitnessVector(1, 1, 1, 1, 1, 1, 1, 0, 1)
        mature = GoalFitnessVector(1, 1, 1, 1, 1, 1, 1, 0, 100)
        self.assertLess(single.extended_component, mature.extended_component)
        one_horizon = score_strategy_over_horizons({"24h": (1.0, 100)})
        all_horizons = score_strategy_over_horizons(
            {"24h": (1.0, 100), "7d": (1.0, 100), "30d": (1.0, 100)}
        )
        self.assertLess(one_horizon.score, all_horizons.score)
        self.assertEqual(one_horizon.horizon_coverage, 1 / 3)


if __name__ == "__main__":
    unittest.main()
