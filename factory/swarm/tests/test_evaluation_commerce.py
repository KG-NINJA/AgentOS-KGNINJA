from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from factory.swarm.commerce import (
    CommerceService,
    PaymentConfig,
    ReceiptSigner,
    VerifiedPayment,
    discovery_documents,
)
from factory.swarm.evaluation import EvaluationEngine, RealizedObservation
from factory.swarm.scheduler import EvaluationScheduler
from factory.swarm.schema import Artifact, ArtifactStatus, ArtifactType, PredictionFields
from factory.swarm.store import ArtifactStore


def make_prediction(index: int, direction: str, confidence: float) -> Artifact:
    return Artifact.create(
        artifact_id=f"prediction-{index}",
        created_at="2026-01-01T00:00:00Z",
        worker_id=f"luna-{index:03d}",
        artifact_type=ArtifactType.PREDICTION.value,
        subject="NVDAc",
        hypothesis=f"Prediction {index}",
        evidence=[{"kind": "fixture", "index": index}],
        source_refs=[f"fixture://prediction/{index}"],
        confidence=confidence,
        time_horizon="24h",
        falsification_condition="Observed direction differs.",
        prediction=PredictionFields(
            asset_or_subject="NVDAc",
            expected_direction=direction,
            expected_magnitude=0.02,
            evaluation_at="2026-01-02T00:00:00Z",
        ),
        metadata={"inference_cost_usd": 0.01, "inference_tokens": 100},
    )


class MappingResolver:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, prediction):
        return self.mapping[prediction.artifact_id]


class FakeVerifier:
    def __init__(self, payment_id="pay_1234567890abcdef1234567890abcdef"):
        self.payment_id = payment_id

    def verify_and_settle(self, payment_signature, requirement):
        if payment_signature != "fixture-signature":
            raise ValueError("bad fixture payment")
        return VerifiedPayment(
            payment_id=self.payment_id,
            settlement_ref="fixture-settlement",
            payer_ref="fixture-payer",
            settled=True,
            payment_response={"success": True, "network": requirement.config.network},
        )


class EvaluationCommerceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(Path(self.temp.name) / "swarm.db")
        self.store.register_workers(count=2, capabilities=["market_analysis"])
        self.first = make_prediction(1, "UP", 0.8)
        self.second = make_prediction(2, "DOWN", 0.7)
        self.store.append_artifact(self.first)
        self.store.append_artifact(self.second)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_maturity_evaluation_metrics_and_budget(self) -> None:
        resolver = MappingResolver(
            {
                self.first.artifact_id: RealizedObservation(
                    0.03, "UP", "2026-01-02T00:01:00Z", ["fixture://outcome/1"]
                ),
                self.second.artifact_id: RealizedObservation(
                    0.01, "UP", "2026-01-02T00:01:00Z", ["fixture://outcome/2"]
                ),
            }
        )
        engine = EvaluationEngine(self.store)
        records = engine.evaluate_due(now="2026-01-03T00:00:00Z", resolver=resolver)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(engine.evaluate_due(now="2026-01-03T00:00:00Z", resolver=resolver)), 0)
        metrics = engine.metrics()
        self.assertEqual(metrics["evaluated_predictions"], 2)
        self.assertEqual(metrics["hit_rate"], 0.5)
        self.assertIsNotNone(metrics["information_coefficient"])
        self.assertEqual(self.store.summary()["evaluation_count"], 2)
        self.assertEqual(self.store.summary()["artifacts_by_type"]["outcome"], 2)
        budgets = {worker.worker_id: worker.virtual_budget for worker in self.store.workers()}
        self.assertGreater(budgets["luna-001"], 100)
        self.assertLess(budgets["luna-002"], 100)

    def _service(self, verifier=None):
        return CommerceService(
            self.store,
            base_url="https://intelligence.example",
            verifier=verifier or FakeVerifier(),
            payment_config=PaymentConfig(
                network="eip155:8453",
                asset="0x1111111111111111111111111111111111111111",
                pay_to="0x2222222222222222222222222222222222222222",
            ),
            receipt_signer=ReceiptSigner(b"x" * 32),
        )

    def test_x402_402_unlock_receipt_and_reuse(self) -> None:
        service = self._service()
        free = service.request(product="signal", subject="NVDAc", level="free")
        self.assertEqual(free.status, 200)
        blocked = service.request(product="signal", subject="NVDAc", level="micro_paid")
        self.assertEqual(blocked.status, 409)
        untrusted_validation = Artifact.create(
            artifact_id="validation-commerce-untrusted",
            created_at="2026-01-01T11:00:00Z",
            worker_id="luna-002",
            artifact_type=ArtifactType.VALIDATION.value,
            subject="NVDAc",
            hypothesis="A worker cannot self-authorize paid publication.",
            evidence=[{"kind": "fixture_validation"}],
            source_refs=["fixture://validation/commerce/untrusted"],
            confidence=1.0,
            time_horizon="24h",
            falsification_condition="A trusted deterministic validator rejects it.",
            parent_artifacts=[self.second.artifact_id],
            status=ArtifactStatus.VALIDATED.value,
        )
        self.store.append_artifact(untrusted_validation)
        still_blocked = service.request(
            product="signal", subject="NVDAc", level="micro_paid"
        )
        self.assertEqual(still_blocked.status, 409)
        validation = Artifact.create(
            artifact_id="validation-commerce-001",
            created_at="2026-01-01T12:00:00Z",
            worker_id="swarm-evaluator",
            artifact_type=ArtifactType.VALIDATION.value,
            subject="NVDAc",
            hypothesis="Fixture commercial quality review passed.",
            evidence=[{"kind": "fixture_validation"}],
            source_refs=["fixture://validation/commerce/1"],
            confidence=1.0,
            time_horizon="24h",
            falsification_condition="A later audited validation rejects the artifact.",
            parent_artifacts=[self.first.artifact_id],
            status=ArtifactStatus.VALIDATED.value,
        )
        self.store.append_artifact(validation)
        required = service.request(product="signal", subject="NVDAc", level="micro_paid")
        self.assertEqual(required.status, 402)
        decoded = json.loads(base64.b64decode(required.headers["PAYMENT-REQUIRED"]))
        self.assertEqual(decoded["x402Version"], 2)
        paid = service.request(
            product="signal",
            subject="NVDAc",
            level="micro_paid",
            payment_signature="fixture-signature",
            consumer_ref="agent-a",
        )
        self.assertEqual(paid.status, 200)
        self.assertTrue(service.receipt_signer.verify(paid.body["receipt"]))
        self.assertIsNone(paid.body["receipt"]["truth_score"])
        self.assertEqual(self.store.purchase_count(self.first.artifact_id), 1)
        reused = service.request(
            product="signal",
            subject="NVDAc",
            level="micro_paid",
            payment_signature="fixture-signature",
            consumer_ref="agent-a",
        )
        self.assertTrue(reused.body["reused_purchase"])
        self.assertEqual(self.store.purchase_count(self.first.artifact_id), 1)

    def test_discovery_reports_payment_state_without_claiming_default_settlement(self) -> None:
        documents = discovery_documents("https://example.test", payment_ready=False)
        self.assertIn("/.well-known/x402/discovery/resources", documents)
        self.assertFalse(documents["/payment-options.json"]["payment_ready"])
        unconfigured = CommerceService(self.store, base_url="https://example.test")
        response = unconfigured.request(
            product="signal", subject="NVDAc", level="micro_paid"
        )
        self.assertEqual(response.status, 503)

    def test_temporal_contamination_is_preserved_but_not_scored(self) -> None:
        resolver = MappingResolver(
            {
                self.first.artifact_id: RealizedObservation(
                    0.03, "UP", "2026-01-01T23:59:00Z", ["fixture://leak/1"]
                ),
                self.second.artifact_id: RealizedObservation(
                    -0.02, "DOWN", "2026-01-01T23:59:00Z", ["fixture://leak/2"]
                ),
            }
        )
        records = EvaluationEngine(self.store).evaluate_due(
            now="2026-01-03T00:00:00Z", resolver=resolver
        )
        self.assertEqual(records, [])
        self.assertEqual(self.store.summary()["evaluation_count"], 0)
        self.assertEqual(self.store.summary()["artifacts_by_type"]["anomaly"], 2)
        anomalies = self.store.list_artifacts(artifact_type=ArtifactType.ANOMALY.value)
        self.assertTrue(all(item.metadata["leakage_guard"] for item in anomalies))

    def test_scheduler_automatically_scores_matured_feed_entries(self) -> None:
        path = Path(self.temp.name) / "outcomes.json"
        path.write_text(
            json.dumps(
                {
                    self.first.artifact_id: {
                        "realized_result": 0.03,
                        "realized_direction": "UP",
                        "observed_at": "2026-01-02T00:01:00Z",
                        "source_refs": ["fixture://scheduled/1"],
                    },
                    self.second.artifact_id: {
                        "realized_result": -0.02,
                        "realized_direction": "DOWN",
                        "observed_at": "2026-01-02T00:01:00Z",
                        "source_refs": ["fixture://scheduled/2"],
                    },
                }
            ),
            encoding="utf-8",
        )
        scheduler = EvaluationScheduler(self.store, path)
        first_cycle = scheduler.run_once(now="2026-01-03T00:00:00Z")
        second_cycle = scheduler.run_once(now="2026-01-03T00:00:01Z")
        self.assertEqual(first_cycle.evaluated_count, 2)
        self.assertEqual(second_cycle.evaluated_count, 0)
        self.assertEqual(first_cycle.metrics["evaluated_predictions"], 2)


if __name__ == "__main__":
    unittest.main()
