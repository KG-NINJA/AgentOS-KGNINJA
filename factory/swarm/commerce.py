"""x402 V2 product catalog with a deny-by-default payment boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from .anti_herding import independent_cluster_vote
from .schema import Artifact, ArtifactStatus, ArtifactType, canonical_json, utc_now
from .store import ArtifactStore


PRODUCT_ENDPOINTS = {
    "signal": "/signal/{subject}",
    "research": "/research/{subject}",
    "risk": "/risk/{subject}",
    "event": "/event/{subject}",
    "consensus": "/consensus/{subject}",
    "counter-thesis": "/counter-thesis/{subject}",
}

PRODUCT_LEVELS: dict[str, dict[str, Any]] = {
    "free": {"price_usd": 0.0, "fields": ["summary", "confidence"]},
    "micro_paid": {
        "price_usd": 0.01,
        "fields": ["evidence", "confidence", "time_horizon"],
    },
    "standard": {
        "price_usd": 0.05,
        "fields": ["full_evidence_graph", "counter_evidence", "historical_calibration"],
    },
    "premium": {
        "price_usd": 0.10,
        "fields": [
            "synthesized_report",
            "prediction_history",
            "disagreement_analysis",
            "machine_readable_artifact_bundle",
        ],
    },
}

TRUSTED_VALIDATOR_IDS = frozenset({"swarm-evaluator", "agentos2-critic"})


@dataclass(frozen=True)
class PaymentConfig:
    network: str
    asset: str
    pay_to: str
    asset_name: str = "USDC"
    asset_version: str = "2"
    max_timeout_seconds: int = 60

    def validate(self) -> None:
        if not self.network or not self.asset or not self.pay_to:
            raise ValueError("network, asset, and pay_to are required")
        if self.max_timeout_seconds < 1:
            raise ValueError("max_timeout_seconds must be positive")


@dataclass(frozen=True)
class PaymentRequirement:
    resource_url: str
    description: str
    amount_usd: float
    config: PaymentConfig

    def as_x402_v2(self) -> dict[str, Any]:
        self.config.validate()
        amount = str(int(round(self.amount_usd * 1_000_000)))
        return {
            "x402Version": 2,
            "error": "PAYMENT-SIGNATURE header is required",
            "resource": {
                "url": self.resource_url,
                "description": self.description,
                "mimeType": "application/json",
            },
            "accepts": [
                {
                    "scheme": "exact",
                    "network": self.config.network,
                    "amount": amount,
                    "asset": self.config.asset,
                    "payTo": self.config.pay_to,
                    "maxTimeoutSeconds": self.config.max_timeout_seconds,
                    "extra": {
                        "name": self.config.asset_name,
                        "version": self.config.asset_version,
                    },
                }
            ],
            "extensions": {
                "payment-identifier": {"required": True},
                "signed-intelligence-receipt": {"version": "1"},
            },
        }


@dataclass(frozen=True)
class VerifiedPayment:
    payment_id: str
    settlement_ref: str
    payer_ref: str
    settled: bool
    payment_response: dict[str, Any]


class PaymentVerifier(Protocol):
    def verify_and_settle(
        self,
        payment_signature: str,
        requirement: PaymentRequirement,
    ) -> VerifiedPayment:
        """Verify and settle outside the LLM context."""


class RejectingPaymentVerifier:
    def verify_and_settle(
        self,
        payment_signature: str,
        requirement: PaymentRequirement,
    ) -> VerifiedPayment:
        raise RuntimeError("x402 payment verifier is not configured")


@dataclass(frozen=True)
class CommerceResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]


class ReceiptSigner:
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("receipt signing key must be at least 32 bytes")
        self._key = key

    def sign(self, payload: dict[str, Any]) -> str:
        return hmac.new(
            self._key,
            canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify(self, receipt: dict[str, Any]) -> bool:
        signature = str(receipt.get("signature", ""))
        payload = {key: value for key, value in receipt.items() if key != "signature"}
        return hmac.compare_digest(signature, self.sign(payload))


class CommerceService:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        base_url: str,
        verifier: PaymentVerifier | None = None,
        payment_config: PaymentConfig | None = None,
        receipt_signer: ReceiptSigner | None = None,
    ):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.verifier = verifier or RejectingPaymentVerifier()
        self.payment_config = payment_config
        self.receipt_signer = receipt_signer

    @property
    def payment_ready(self) -> bool:
        return (
            self.payment_config is not None
            and self.receipt_signer is not None
            and not isinstance(self.verifier, RejectingPaymentVerifier)
        )

    def catalog(self) -> dict[str, Any]:
        return {
            "name": "AgentOS2 Luna Swarm Intelligence",
            "research_only": True,
            "truth_usefulness_demand_separated": True,
            "x402_version": 2,
            "payment_ready": self.payment_ready,
            "endpoints": PRODUCT_ENDPOINTS,
            "product_levels": PRODUCT_LEVELS,
            "headers": {
                "server_payment_required": "PAYMENT-REQUIRED",
                "client_payment_signature": "PAYMENT-SIGNATURE",
                "server_payment_response": "PAYMENT-RESPONSE",
            },
        }

    def request(
        self,
        *,
        product: str,
        subject: str,
        level: str,
        payment_signature: str | None = None,
        consumer_ref: str = "anonymous",
    ) -> CommerceResponse:
        if product not in PRODUCT_ENDPOINTS:
            return CommerceResponse(404, {"error": "unknown_product"}, {})
        if level not in PRODUCT_LEVELS:
            return CommerceResponse(400, {"error": "unknown_product_level"}, {})
        if level != "free" and not self.payment_ready:
            return CommerceResponse(
                503,
                {
                    "error": "x402_not_configured",
                    "message": "Discovery is active, but paid settlement is disabled.",
                },
                {},
            )
        artifact = self._select_artifact(
            product,
            subject,
            require_commercial_validation=level != "free",
        )
        if artifact is None:
            if level != "free" and self._select_artifact(
                product, subject, require_commercial_validation=False
            ) is not None:
                return CommerceResponse(
                    409,
                    {
                        "error": "artifact_not_commercially_validated",
                        "message": (
                            "Paid access requires a deterministic evaluation or an explicit "
                            "validation artifact."
                        ),
                    },
                    {},
                )
            return CommerceResponse(404, {"error": "artifact_not_found"}, {})
        if level == "free":
            return CommerceResponse(200, self._render(product, level, artifact), {})
        assert self.payment_config is not None
        requirement = PaymentRequirement(
            resource_url=(
                f"{self.base_url}{PRODUCT_ENDPOINTS[product].format(subject=subject)}?level={level}"
            ),
            description=f"{level} {product} intelligence for {subject}",
            amount_usd=float(PRODUCT_LEVELS[level]["price_usd"]),
            config=self.payment_config,
        )
        payment_required = requirement.as_x402_v2()
        encoded_required = base64.b64encode(canonical_json(payment_required).encode()).decode()
        if not payment_signature:
            return CommerceResponse(
                402,
                payment_required,
                {"PAYMENT-REQUIRED": encoded_required},
            )
        try:
            verified = self.verifier.verify_and_settle(payment_signature, requirement)
        except Exception as exc:
            return CommerceResponse(
                402,
                {"error": "payment_verification_failed", "reason": type(exc).__name__},
                {"PAYMENT-REQUIRED": encoded_required},
            )
        if not verified.settled:
            return CommerceResponse(
                402,
                {"error": "payment_not_settled"},
                {"PAYMENT-REQUIRED": encoded_required},
            )
        previous = self.store.purchase_by_payment_id(verified.payment_id)
        if previous is not None:
            self.store.record_receipt_reuse(previous["purchase_id"], consumer_ref)
            return CommerceResponse(
                200,
                {
                    "artifact": self._render(product, level, artifact),
                    "receipt": previous["receipt"],
                    "reused_purchase": True,
                },
                {
                    "PAYMENT-RESPONSE": base64.b64encode(
                        canonical_json(verified.payment_response).encode()
                    ).decode()
                },
            )
        receipt_core = {
            "receipt_version": "1",
            "purchase_id": f"purchase_{uuid.uuid4().hex}",
            "payment_id": verified.payment_id,
            "settlement_ref": verified.settlement_ref,
            "payer_ref_hash": hashlib.sha256(verified.payer_ref.encode()).hexdigest(),
            "artifact_id": artifact.artifact_id,
            "artifact_hash": artifact.record_hash,
            "product": product,
            "product_level": level,
            "amount_usd": float(PRODUCT_LEVELS[level]["price_usd"]),
            "issued_at": utc_now(),
            "truth_score": self._truth_score(artifact),
            "demand_event": 1,
        }
        assert self.receipt_signer is not None
        receipt = {**receipt_core, "signature": self.receipt_signer.sign(receipt_core)}
        self.store.append_purchase(
            artifact_id=artifact.artifact_id,
            product_level=level,
            amount_usd=receipt_core["amount_usd"],
            payment_id=verified.payment_id,
            receipt_payload=receipt,
            purchase_id=receipt_core["purchase_id"],
        )
        workers = {worker.worker_id for worker in self.store.workers()}
        if artifact.worker_id in workers:
            self.store.append_budget_event(
                worker_id=artifact.worker_id,
                delta=1.0,
                reason="commercial_purchase_demand_signal",
                related_artifact_id=artifact.artifact_id,
            )
        return CommerceResponse(
            200,
            {"artifact": self._render(product, level, artifact), "receipt": receipt},
            {
                "PAYMENT-RESPONSE": base64.b64encode(
                    canonical_json(verified.payment_response).encode()
                ).decode()
            },
        )

    def _select_artifact(
        self,
        product: str,
        subject: str,
        *,
        require_commercial_validation: bool,
    ) -> Artifact | None:
        artifacts = self.store.list_artifacts(subject=subject, limit=10_000, newest_first=True)
        if product == "counter-thesis":
            allowed = {ArtifactType.COUNTER_EVIDENCE.value, ArtifactType.CRITIQUE.value}
        elif product == "event":
            allowed = {
                ArtifactType.MARKET_OBSERVATION.value,
                ArtifactType.TECHNOLOGY_SIGNAL.value,
                ArtifactType.COMPANY_SIGNAL.value,
                ArtifactType.ANOMALY.value,
            }
        elif product == "risk":
            allowed = {ArtifactType.CRITIQUE.value, ArtifactType.ANOMALY.value}
        elif product == "signal":
            allowed = {ArtifactType.PREDICTION.value, ArtifactType.STRATEGY.value}
        elif product == "consensus":
            allowed = {ArtifactType.PREDICTION.value, ArtifactType.STRATEGY.value}
        elif product == "research":
            allowed = {
                ArtifactType.EVIDENCE.value,
                ArtifactType.MARKET_OBSERVATION.value,
                ArtifactType.TECHNOLOGY_SIGNAL.value,
                ArtifactType.COMPANY_SIGNAL.value,
                ArtifactType.STRATEGY.value,
                ArtifactType.COMMERCIAL_PRODUCT.value,
            }
        else:
            allowed = set()
        excluded_statuses = {
            ArtifactStatus.FAILED.value,
            ArtifactStatus.REJECTED.value,
            ArtifactStatus.INVALIDATED.value,
            ArtifactStatus.ARCHIVED.value,
        }
        candidates = [
            artifact
            for artifact in artifacts
            if artifact.artifact_type in allowed
            and artifact.status not in excluded_statuses
            and (
                not require_commercial_validation
                or self._is_commercially_validated(artifact)
            )
        ]
        return (
            max(
                candidates,
                key=lambda item: (
                    self._quality_score(item),
                    item.created_at,
                    item.artifact_id,
                ),
            )
            if candidates
            else None
        )

    def _is_commercially_validated(self, artifact: Artifact) -> bool:
        if (
            artifact.status
            in {
                ArtifactStatus.VALIDATED.value,
                ArtifactStatus.EVALUATED.value,
                ArtifactStatus.COMMERCIALIZED.value,
            }
            and artifact.worker_id in TRUSTED_VALIDATOR_IDS
        ):
            return True
        if self._truth_score(artifact) is not None:
            return True
        for artifact_id in self.store.lineage(artifact.artifact_id)["descendants"]:
            descendant = self.store.get_artifact(artifact_id)
            if (
                descendant is not None
                and descendant.worker_id in TRUSTED_VALIDATOR_IDS
                and descendant.status
                in {
                    ArtifactStatus.VALIDATED.value,
                    ArtifactStatus.EVALUATED.value,
                }
            ):
                return True
        return False

    def _quality_score(self, artifact: Artifact) -> float:
        truth = self._truth_score(artifact)
        if truth is not None:
            return truth
        if self._is_commercially_validated(artifact):
            return 0.5
        return 0.0

    def _render(self, product: str, level: str, artifact: Artifact) -> dict[str, Any]:
        base = {
            "artifact_id": artifact.artifact_id,
            "subject": artifact.subject,
            "summary": artifact.hypothesis,
            "confidence": artifact.confidence,
            "research_only": True,
        }
        if level == "free":
            return base
        base.update(
            {
                "evidence": artifact.evidence,
                "source_refs": artifact.source_refs,
                "time_horizon": artifact.time_horizon,
                "falsification_condition": artifact.falsification_condition,
            }
        )
        if level in {"standard", "premium"}:
            lineage = self.store.lineage(artifact.artifact_id)
            history = self.store.list_artifacts(subject=artifact.subject, limit=1_000)
            base.update(
                {
                    "full_evidence_graph": lineage,
                    "counter_evidence": [
                        item.to_dict()
                        for item in history
                        if item.artifact_type == ArtifactType.COUNTER_EVIDENCE.value
                    ],
                    "historical_calibration": self._truth_score(artifact),
                }
            )
        if level == "premium":
            predictions = [
                item
                for item in self.store.list_artifacts(subject=artifact.subject, limit=1_000)
                if item.artifact_type == ArtifactType.PREDICTION.value
            ]
            base.update(
                {
                    "synthesized_report": artifact.hypothesis,
                    "prediction_history": [item.to_dict() for item in predictions],
                    "disagreement_analysis": independent_cluster_vote(predictions),
                    "machine_readable_artifact_bundle": artifact.to_dict(),
                }
            )
        return base

    def _truth_score(self, artifact: Artifact) -> float | None:
        identifiers = [artifact.artifact_id]
        identifiers.extend(self.store.lineage(artifact.artifact_id)["ancestors"])
        scores = []
        for artifact_id in identifiers:
            evaluation = self.store.evaluation_for(artifact_id)
            if evaluation is not None:
                scores.append(evaluation.calibration_score)
        return sum(scores) / len(scores) if scores else None


def discovery_documents(base_url: str, payment_ready: bool) -> dict[str, Any]:
    levels = PRODUCT_LEVELS
    resources = [
        {
            "method": "GET",
            "path": path,
            "levels": list(levels),
            "x402": {"version": 2, "payment_ready": payment_ready},
        }
        for path in PRODUCT_ENDPOINTS.values()
    ]
    agent = {
        "name": "AgentOS2 Luna Swarm Intelligence",
        "description": "Artifact-derived research intelligence; no trading execution.",
        "base_url": base_url.rstrip("/"),
        "discovery": {
            "pricing": "/pricing.json",
            "openapi": "/openapi.json",
            "x402_resources": "/.well-known/x402/discovery/resources",
            "llms": "/llms.txt",
        },
    }
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": agent["name"], "version": "0.1.0"},
        "paths": {
            path.replace("{subject}", "{subject}"): {
                "get": {
                    "parameters": [
                        {"name": "subject", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "level", "in": "query", "schema": {"enum": list(levels)}},
                    ],
                    "responses": {"200": {"description": "Intelligence artifact"}, "402": {"description": "Payment required"}},
                }
            }
            for path in PRODUCT_ENDPOINTS.values()
        },
    }
    return {
        "/agent.json": agent,
        "/.well-known/agent.json": agent,
        "/payment-options.json": {
            "protocol": "x402",
            "version": 2,
            "payment_ready": payment_ready,
            "headers": ["PAYMENT-REQUIRED", "PAYMENT-SIGNATURE", "PAYMENT-RESPONSE"],
        },
        "/pricing.json": {"currency": "USD", "levels": levels},
        "/openapi.json": openapi,
        "/.well-known/x402/discovery/resources": {"resources": resources},
        "/llms.txt": (
            "# AgentOS2 Luna Swarm Intelligence\n"
            "Research-only intelligence artifacts. No investment advice or execution.\n"
            "Discovery: /.well-known/agent.json\n"
            "Pricing: /pricing.json\n"
            "x402 resources: /.well-known/x402/discovery/resources\n"
        ),
    }
