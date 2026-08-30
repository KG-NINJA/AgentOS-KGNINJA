"""Canonical, validation-first schemas for the Swarm Artifact Environment."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class SchemaError(ValueError):
    """Raised when an artifact cannot enter the shared environment."""


class ArtifactType(str, Enum):
    PREDICTION = "prediction"
    EVIDENCE = "evidence"
    COUNTER_EVIDENCE = "counter_evidence"
    MARKET_OBSERVATION = "market_observation"
    TECHNOLOGY_SIGNAL = "technology_signal"
    COMPANY_SIGNAL = "company_signal"
    ANOMALY = "anomaly"
    STRATEGY = "strategy"
    CRITIQUE = "critique"
    VALIDATION = "validation"
    OUTCOME = "outcome"
    COMMERCIAL_PRODUCT = "commercial_product"


class ArtifactStatus(str, Enum):
    ACTIVE = "ACTIVE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"
    EVALUATED = "EVALUATED"
    COMMERCIALIZED = "COMMERCIALIZED"
    ARCHIVED = "ARCHIVED"


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    NO_SIGNAL = "NO_SIGNAL"


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_SECRET_KEY_RE = re.compile(
    r"(^|_)(private_?key|mnemonic|seed_?phrase|api_?key|access_?token|secret)(_|$)",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:MNEMONIC|PRIVATE_KEY|API_KEY|SECRET)\s*=\s*\S+", re.IGNORECASE),
    re.compile(
        r"(?:[?&](?:api[_-]?key|access[_-]?token|token|secret|signature|private[_-]?key)=)[^&#\s]+",
        re.IGNORECASE,
    ),
    re.compile(r"\bAuthorization\s*:\s*Bearer\s+\S+", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _assert_no_secrets(value: Any, path: str = "artifact") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text):
                raise SchemaError(f"secret-like key is forbidden at {path}.{key_text}")
            _assert_no_secrets(child, f"{path}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secrets(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_RES:
            if pattern.search(value):
                raise SchemaError(f"secret-like value is forbidden at {path}")


def _validate_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SchemaError(f"{field_name} has an invalid identifier")


def _validate_probability(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{field_name} must be numeric")
    if not 0.0 <= float(value) <= 1.0:
        raise SchemaError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class PredictionFields:
    asset_or_subject: str
    expected_direction: str
    expected_magnitude: float
    evaluation_at: str

    def validate(self, *, created_at: str) -> None:
        if not self.asset_or_subject.strip():
            raise SchemaError("asset_or_subject is required for predictions")
        try:
            Direction(self.expected_direction)
        except ValueError as exc:
            raise SchemaError("expected_direction is invalid") from exc
        if isinstance(self.expected_magnitude, bool) or not isinstance(
            self.expected_magnitude, (int, float)
        ):
            raise SchemaError("expected_magnitude must be numeric")
        if not math.isfinite(float(self.expected_magnitude)):
            raise SchemaError("expected_magnitude must be finite")
        created = parse_timestamp(created_at, "created_at")
        evaluation = parse_timestamp(self.evaluation_at, "evaluation_at")
        if evaluation <= created:
            raise SchemaError("evaluation_at must be later than created_at")
        if self.expected_direction == Direction.NO_SIGNAL.value and float(
            self.expected_magnitude
        ) != 0.0:
            raise SchemaError("NO_SIGNAL expected_magnitude must be 0")


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    created_at: str
    worker_id: str
    artifact_type: str
    subject: str
    hypothesis: str
    evidence: list[Any]
    source_refs: list[str]
    confidence: float
    time_horizon: str
    falsification_condition: str
    parent_artifacts: list[str]
    derived_from: list[str]
    status: str
    prediction: PredictionFields | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        worker_id: str,
        artifact_type: str,
        subject: str,
        hypothesis: str,
        evidence: list[Any],
        source_refs: list[str],
        confidence: float,
        time_horizon: str,
        falsification_condition: str,
        parent_artifacts: list[str] | None = None,
        derived_from: list[str] | None = None,
        status: str = ArtifactStatus.ACTIVE.value,
        prediction: PredictionFields | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
        created_at: str | None = None,
    ) -> "Artifact":
        artifact = cls(
            artifact_id=artifact_id or f"art_{uuid.uuid4().hex}",
            created_at=created_at or utc_now(),
            worker_id=worker_id,
            artifact_type=artifact_type,
            subject=subject,
            hypothesis=hypothesis,
            evidence=list(evidence),
            source_refs=list(source_refs),
            confidence=float(confidence),
            time_horizon=time_horizon,
            falsification_condition=falsification_condition,
            parent_artifacts=list(parent_artifacts or []),
            derived_from=list(derived_from or []),
            status=status,
            prediction=prediction,
            metadata=dict(metadata or {}),
        )
        artifact.validate()
        return artifact

    def validate(self) -> None:
        _validate_id(self.artifact_id, "artifact_id")
        _validate_id(self.worker_id, "worker_id")
        parse_timestamp(self.created_at, "created_at")
        try:
            ArtifactType(self.artifact_type)
        except ValueError as exc:
            raise SchemaError("artifact_type is invalid") from exc
        try:
            ArtifactStatus(self.status)
        except ValueError as exc:
            raise SchemaError("status is invalid") from exc
        for field_name, value in (
            ("subject", self.subject),
            ("hypothesis", self.hypothesis),
            ("time_horizon", self.time_horizon),
            ("falsification_condition", self.falsification_condition),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SchemaError(f"{field_name} is required")
        if not isinstance(self.evidence, list):
            raise SchemaError("evidence must be a list")
        if not isinstance(self.source_refs, list) or not all(
            isinstance(item, str) and item.strip() for item in self.source_refs
        ):
            raise SchemaError("source_refs must be a list of non-empty strings")
        if not self.source_refs:
            raise SchemaError("source_refs are required for provenance")
        _validate_probability(self.confidence, "confidence")
        lineage = self.parent_artifacts + self.derived_from
        if self.artifact_id in lineage:
            raise SchemaError("an artifact cannot reference itself")
        if len(lineage) != len(set(lineage)):
            raise SchemaError("lineage references must not be duplicated")
        for artifact_id in lineage:
            _validate_id(artifact_id, "lineage artifact id")
        if self.artifact_type == ArtifactType.PREDICTION.value:
            if self.prediction is None:
                raise SchemaError("prediction fields are required")
            self.prediction.validate(created_at=self.created_at)
        elif self.prediction is not None:
            raise SchemaError("prediction fields are only valid for prediction artifacts")
        _assert_no_secrets(self.to_dict())
        if len(canonical_json(self.to_dict()).encode("utf-8")) > 256_000:
            raise SchemaError("artifact exceeds the 256 KiB limit")

    def to_dict(self) -> dict[str, Any]:
        data = {
            "artifact_id": self.artifact_id,
            "created_at": self.created_at,
            "worker_id": self.worker_id,
            "artifact_type": self.artifact_type,
            "subject": self.subject,
            "hypothesis": self.hypothesis,
            "evidence": self.evidence,
            "source_refs": self.source_refs,
            "confidence": self.confidence,
            "time_horizon": self.time_horizon,
            "falsification_condition": self.falsification_condition,
            "parent_artifacts": self.parent_artifacts,
            "derived_from": self.derived_from,
            "status": self.status,
            "metadata": self.metadata,
        }
        if self.prediction is not None:
            data.update(asdict(self.prediction))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Artifact":
        prediction = None
        if data.get("artifact_type") == ArtifactType.PREDICTION.value:
            prediction = PredictionFields(
                asset_or_subject=str(data.get("asset_or_subject", "")),
                expected_direction=str(data.get("expected_direction", "")),
                expected_magnitude=data.get("expected_magnitude"),
                evaluation_at=str(data.get("evaluation_at", "")),
            )
        artifact = cls(
            artifact_id=str(data.get("artifact_id", "")),
            created_at=str(data.get("created_at", "")),
            worker_id=str(data.get("worker_id", "")),
            artifact_type=str(data.get("artifact_type", "")),
            subject=str(data.get("subject", "")),
            hypothesis=str(data.get("hypothesis", "")),
            evidence=list(data.get("evidence", [])),
            source_refs=list(data.get("source_refs", [])),
            confidence=data.get("confidence"),
            time_horizon=str(data.get("time_horizon", "")),
            falsification_condition=str(data.get("falsification_condition", "")),
            parent_artifacts=list(data.get("parent_artifacts", [])),
            derived_from=list(data.get("derived_from", [])),
            status=str(data.get("status", "")),
            prediction=prediction,
            metadata=dict(data.get("metadata", {})),
        )
        artifact.validate()
        return artifact

    @property
    def record_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    prediction_artifact_id: str
    realized_result: float
    realized_direction: str
    prediction_error: float
    brier_score: float
    calibration_score: float
    usefulness_score: float
    uniqueness_score: float
    cost: float
    token_count: int
    evaluated_at: str
    source_refs: list[str]
    evaluator_version: str = "swarm-eval-v1"

    def validate(self) -> None:
        _validate_id(self.evaluation_id, "evaluation_id")
        _validate_id(self.prediction_artifact_id, "prediction_artifact_id")
        parse_timestamp(self.evaluated_at, "evaluated_at")
        try:
            Direction(self.realized_direction)
        except ValueError as exc:
            raise SchemaError("realized_direction is invalid") from exc
        for name, value in (
            ("realized_result", self.realized_result),
            ("prediction_error", self.prediction_error),
            ("cost", self.cost),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError(f"{name} must be numeric")
            if not math.isfinite(float(value)):
                raise SchemaError(f"{name} must be finite")
        for name, value in (
            ("brier_score", self.brier_score),
            ("calibration_score", self.calibration_score),
            ("usefulness_score", self.usefulness_score),
            ("uniqueness_score", self.uniqueness_score),
        ):
            _validate_probability(value, name)
        if self.cost < 0:
            raise SchemaError("cost cannot be negative")
        if self.token_count < 0:
            raise SchemaError("token_count cannot be negative")
        if not self.source_refs:
            raise SchemaError("evaluation source_refs are required")
        _assert_no_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def record_hash(self) -> str:
        return canonical_hash(self.to_dict())
