#!/usr/bin/env python3
"""Operational CLI for the AgentOS2 swarm extension."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .agentos_adapter import GoalEcologyAdapter
from .baselines import CandidatePrediction, compare_baselines
from .fitness import GoalFitnessVector
from .orchestration import DeterministicLunaTestDouble, SwarmOrchestrator
from .scheduler import EvaluationScheduler
from .schema import Artifact
from .store import ArtifactStore


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(type(value).__name__)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))


def _store(args: argparse.Namespace) -> ArtifactStore:
    return ArtifactStore(Path(args.db))


def cmd_init(args: argparse.Namespace) -> int:
    store = _store(args)
    orchestrator = SwarmOrchestrator(
        store,
        DeterministicLunaTestDouble(),
        initial_worker_count=args.workers,
    )
    worker_ids = orchestrator.bootstrap()
    _print({"initialized_workers": len(worker_ids), "store": str(store.path), **store.summary()})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _print(_store(args).summary())
    return 0


def cmd_ingest_artifact(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    artifact = Artifact.from_dict(data)
    record_hash = _store(args).append_artifact(artifact)
    _print({"artifact_id": artifact.artifact_id, "record_hash": record_hash})
    return 0


def cmd_record_goal_signal(args: argparse.Namespace) -> int:
    result = GoalEcologyAdapter(_store(args)).record_event(
        goal_id=args.goal_id,
        event_name=args.event,
        subject=args.subject,
        notes=args.notes,
        source_ref=args.source_ref,
    )
    _print(result)
    return 0


def cmd_record_goal_fitness(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    vector = GoalFitnessVector(**payload)
    adapter = GoalEcologyAdapter(_store(args))
    snapshot_id = adapter.record_extended_fitness(
        goal_id=args.goal_id,
        vector=vector,
        artifact_id=args.artifact_id,
    )
    _print(
        {
            "goal_id": args.goal_id,
            "snapshot_id": snapshot_id,
            "total": vector.total,
            "truth_usefulness_demand": asdict(vector.axes),
        }
    )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    cycle = EvaluationScheduler(_store(args), args.outcomes).run_once(now=args.now)
    _print(cycle)
    return 0


def cmd_evaluation_daemon(args: argparse.Namespace) -> int:
    scheduler = EvaluationScheduler(_store(args), args.outcomes)
    try:
        scheduler.run_forever(
            poll_seconds=args.poll_seconds,
            on_cycle=lambda cycle: _print(cycle),
        )
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_demo_round(args: argparse.Namespace) -> int:
    if not args.allow_test_double:
        raise SystemExit(
            "demo-round requires --allow-test-double; it does not run real Luna inference"
        )
    store = _store(args)
    orchestrator = SwarmOrchestrator(
        store,
        DeterministicLunaTestDouble(),
        initial_worker_count=args.workers,
        max_parallel=args.parallel,
    )
    orchestrator.bootstrap()
    result = orchestrator.run_round(
        subject=args.subject,
        time_horizon=args.horizon,
        worker_limit=args.workers,
    )
    _print({"test_double": True, **asdict(result), "store_summary": store.summary()})
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("comparison file must contain a JSON list")
    result = compare_baselines(CandidatePrediction(**row) for row in rows)
    _print(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    default_db = os.environ.get(
        "AGENTOS2_SWARM_DB",
        str(root / "runtime" / "swarm" / "swarm.db"),
    )
    parser = argparse.ArgumentParser(description="AgentOS2 stigmergic swarm research")
    parser.add_argument("--db", default=default_db)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create store and homogeneous worker population")
    init.add_argument("--workers", type=int, default=50)
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="show append-only store counts")
    status.set_defaults(func=cmd_status)

    ingest = sub.add_parser("ingest-artifact", help="validate and append one artifact JSON")
    ingest.add_argument("--file", required=True)
    ingest.set_defaults(func=cmd_ingest_artifact)

    feedback = sub.add_parser(
        "record-goal-signal", help="bridge the original AgentOS2 artifact feedback"
    )
    feedback.add_argument("--goal-id", required=True)
    feedback.add_argument(
        "--event", required=True, choices=("artifact_generated", "critic_pass", "critic_fail")
    )
    feedback.add_argument("--subject", required=True)
    feedback.add_argument("--notes", required=True)
    feedback.add_argument("--source-ref", required=True)
    feedback.set_defaults(func=cmd_record_goal_signal)

    extended = sub.add_parser(
        "record-goal-fitness", help="append an extended goal fitness snapshot"
    )
    extended.add_argument("--goal-id", required=True)
    extended.add_argument("--file", required=True)
    extended.add_argument("--artifact-id")
    extended.set_defaults(func=cmd_record_goal_fitness)

    evaluate = sub.add_parser(
        "evaluate", help="evaluate every matured prediction present in an outcome mapping"
    )
    evaluate.add_argument("--outcomes", required=True)
    evaluate.add_argument("--now")
    evaluate.set_defaults(func=cmd_evaluate)

    daemon = sub.add_parser(
        "evaluation-daemon",
        help="poll an audited outcome feed and score predictions after maturity",
    )
    daemon.add_argument("--outcomes", required=True)
    daemon.add_argument("--poll-seconds", type=float, default=60.0)
    daemon.set_defaults(func=cmd_evaluation_daemon)

    demo = sub.add_parser("demo-round", help="offline orchestration smoke test")
    demo.add_argument("--allow-test-double", action="store_true")
    demo.add_argument("--workers", type=int, default=50)
    demo.add_argument("--parallel", type=int, default=10)
    demo.add_argument("--subject", required=True)
    demo.add_argument("--horizon", default="24h")
    demo.set_defaults(func=cmd_demo_round)

    compare = sub.add_parser("compare", help="run matched baseline comparison")
    compare.add_argument("--file", required=True)
    compare.set_defaults(func=cmd_compare)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
