"""Evidence-aware selection that avoids treating correlated outputs as votes."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .schema import Artifact, ArtifactType, Direction


def normalize_source_ref(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))
    return value.lower()


@dataclass(frozen=True)
class EvidenceCluster:
    cluster_id: str
    artifact_ids: tuple[str, ...]
    independent_source_refs: tuple[str, ...]


class _UnionFind:
    def __init__(self, identifiers: Iterable[str]):
        self.parent = {identifier: identifier for identifier in identifiers}

    def find(self, value: str) -> str:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            next_value = self.parent[value]
            self.parent[value] = root
            value = next_value
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _root_sources(
    artifact: Artifact,
    by_id: Mapping[str, Artifact],
    visiting: set[str] | None = None,
) -> set[str]:
    visiting = set(visiting or ())
    if artifact.artifact_id in visiting:
        return set()
    visiting.add(artifact.artifact_id)
    own = {normalize_source_ref(ref) for ref in artifact.source_refs}
    parents = artifact.parent_artifacts + artifact.derived_from
    inherited: set[str] = set()
    for parent_id in parents:
        parent = by_id.get(parent_id)
        if parent is not None:
            inherited.update(_root_sources(parent, by_id, visiting))
    return inherited | own


def cluster_by_evidence(artifacts: Iterable[Artifact]) -> list[EvidenceCluster]:
    items = list(artifacts)
    by_id = {artifact.artifact_id: artifact for artifact in items}
    union = _UnionFind(by_id)
    sources = {artifact.artifact_id: _root_sources(artifact, by_id) for artifact in items}
    source_owner: dict[str, str] = {}
    for artifact in items:
        for source in sources[artifact.artifact_id]:
            owner = source_owner.setdefault(source, artifact.artifact_id)
            union.union(owner, artifact.artifact_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for artifact_id in by_id:
        groups[union.find(artifact_id)].append(artifact_id)
    output: list[EvidenceCluster] = []
    for identifiers in groups.values():
        identifiers.sort()
        refs = sorted({source for identifier in identifiers for source in sources[identifier]})
        digest = hashlib.sha256("\n".join(refs + identifiers).encode("utf-8")).hexdigest()[:16]
        output.append(
            EvidenceCluster(
                cluster_id=f"ecluster_{digest}",
                artifact_ids=tuple(identifiers),
                independent_source_refs=tuple(refs),
            )
        )
    return sorted(output, key=lambda cluster: cluster.cluster_id)


def prediction_direction(artifact: Artifact) -> str | None:
    if artifact.artifact_type != ArtifactType.PREDICTION.value or artifact.prediction is None:
        return None
    return artifact.prediction.expected_direction


def independent_cluster_vote(artifacts: Iterable[Artifact]) -> dict[str, int]:
    """Return at most one directional vote for each evidence cluster."""

    items = list(artifacts)
    by_id = {artifact.artifact_id: artifact for artifact in items}
    votes: Counter[str] = Counter()
    for cluster in cluster_by_evidence(items):
        predictions = [
            by_id[artifact_id]
            for artifact_id in cluster.artifact_ids
            if prediction_direction(by_id[artifact_id]) is not None
        ]
        if not predictions:
            continue
        representative = max(
            predictions,
            key=lambda artifact: (artifact.confidence, artifact.artifact_id),
        )
        votes[prediction_direction(representative)] += 1
    return dict(votes)


def select_observations(
    artifacts: Iterable[Artifact],
    *,
    limit: int,
    minority_fraction: float = 0.2,
    quality: Mapping[str, float] | None = None,
) -> list[Artifact]:
    """Select strong artifacts while reserving capacity for disagreement.

    Reuse counts and popularity are deliberately absent from the ranking key.
    Correlated evidence chains contribute one representative before duplicates.
    """

    if limit < 1:
        raise ValueError("limit must be positive")
    if not 0.0 <= minority_fraction <= 0.5:
        raise ValueError("minority_fraction must be between 0 and 0.5")
    items = list(artifacts)
    if len(items) <= limit:
        return sorted(items, key=lambda artifact: artifact.artifact_id)
    by_id = {artifact.artifact_id: artifact for artifact in items}
    quality = quality or {}
    cluster_representatives: list[Artifact] = []
    for cluster in cluster_by_evidence(items):
        cluster_representatives.append(
            max(
                (by_id[artifact_id] for artifact_id in cluster.artifact_ids),
                key=lambda artifact: (
                    float(quality.get(artifact.artifact_id, 0.5)),
                    artifact.confidence,
                    artifact.artifact_type == ArtifactType.COUNTER_EVIDENCE.value,
                    artifact.artifact_id,
                ),
            )
        )

    direction_counts = Counter(
        direction
        for direction in (prediction_direction(artifact) for artifact in cluster_representatives)
        if direction is not None and direction != Direction.NO_SIGNAL.value
    )
    majority_direction = (
        max(direction_counts, key=lambda direction: (direction_counts[direction], direction))
        if direction_counts
        else None
    )
    minority = [
        artifact
        for artifact in cluster_representatives
        if artifact.artifact_type == ArtifactType.COUNTER_EVIDENCE.value
        or (
            prediction_direction(artifact) is not None
            and prediction_direction(artifact) != majority_direction
        )
    ]
    majority = [artifact for artifact in cluster_representatives if artifact not in minority]

    def rank(artifact: Artifact) -> tuple[float, float, int, str]:
        return (
            float(quality.get(artifact.artifact_id, 0.5)),
            artifact.confidence,
            int(artifact.artifact_type == ArtifactType.COUNTER_EVIDENCE.value),
            artifact.artifact_id,
        )

    minority.sort(key=rank, reverse=True)
    majority.sort(key=rank, reverse=True)
    reserve = min(len(minority), ceil(limit * minority_fraction))
    selected = minority[:reserve]
    selected.extend(majority[: max(0, limit - len(selected))])
    if len(selected) < limit:
        remaining = [item for item in minority[reserve:] if item not in selected]
        selected.extend(remaining[: limit - len(selected)])
    if len(selected) < limit:
        duplicates = sorted(
            (item for item in items if item not in selected), key=rank, reverse=True
        )
        selected.extend(duplicates[: limit - len(selected)])
    return selected[:limit]


def create_disagreement_artifact(
    *,
    worker_id: str,
    subject: str,
    predictions: Iterable[Artifact],
) -> Artifact:
    items = [artifact for artifact in predictions if prediction_direction(artifact)]
    votes = independent_cluster_vote(items)
    if len([value for value in votes.values() if value > 0]) < 2:
        raise ValueError("no independent disagreement exists")
    return Artifact.create(
        worker_id=worker_id,
        artifact_type=ArtifactType.COUNTER_EVIDENCE.value,
        subject=subject,
        hypothesis="Independent evidence chains disagree and consensus is not decisive.",
        evidence=[{"kind": "independent_cluster_vote", "votes": votes}],
        source_refs=sorted({ref for artifact in items for ref in artifact.source_refs}),
        confidence=0.5,
        time_horizon=items[0].time_horizon,
        falsification_condition="Independent evidence clusters converge after new observations.",
        parent_artifacts=[artifact.artifact_id for artifact in items],
        derived_from=[],
        metadata={"independent_votes": votes},
    )
