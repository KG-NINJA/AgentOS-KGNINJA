"""Automatic maturity evaluation over an externally audited outcome feed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .evaluation import EvaluationEngine, RealizedObservation
from .schema import Artifact, utc_now
from .store import ArtifactStore


class JsonOutcomeFileResolver:
    """Read-only bridge for connector-produced, provenance-audited outcomes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.mapping = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("outcomes file must map artifact_id to observations")
        return payload

    def resolve(self, prediction: Artifact) -> RealizedObservation | None:
        payload = self.mapping.get(prediction.artifact_id)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("outcome mapping values must be JSON objects")
        return RealizedObservation(**payload)


@dataclass(frozen=True)
class EvaluationCycle:
    evaluated_count: int
    evaluation_ids: tuple[str, ...]
    run_at: str
    metrics: dict[str, float | int | None | str]


class EvaluationScheduler:
    def __init__(self, store: ArtifactStore, outcome_path: str | Path):
        self.store = store
        self.outcome_path = Path(outcome_path)

    def run_once(self, *, now: str | None = None) -> EvaluationCycle:
        run_at = now or utc_now()
        engine = EvaluationEngine(self.store)
        records = engine.evaluate_due(
            now=run_at,
            resolver=JsonOutcomeFileResolver(self.outcome_path),
        )
        return EvaluationCycle(
            evaluated_count=len(records),
            evaluation_ids=tuple(record.evaluation_id for record in records),
            run_at=run_at,
            metrics=engine.metrics(),
        )

    def run_forever(
        self,
        *,
        poll_seconds: float = 60.0,
        stop_event: Event | None = None,
        on_cycle: Callable[[EvaluationCycle], None] | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        stopper = stop_event or Event()
        while not stopper.is_set():
            cycle = self.run_once()
            if on_cycle is not None:
                on_cycle(cycle)
            stopper.wait(poll_seconds)
