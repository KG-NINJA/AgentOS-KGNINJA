"""Stigmergic Luna swarm research extension for AgentOS2.

The package is intentionally stdlib-only.  It stores research artifacts and
feedback, but it contains no wallet/private-key handling, live payment
settlement adapter, or live-trading implementation.  Receipt authentication is
limited to an application-level HMAC boundary supplied outside artifact data.
"""

from .schema import Artifact, ArtifactType, EvaluationRecord, PredictionFields
from .store import ArtifactStore

__all__ = [
    "Artifact",
    "ArtifactStore",
    "ArtifactType",
    "EvaluationRecord",
    "PredictionFields",
]
