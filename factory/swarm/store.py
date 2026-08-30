"""SQLite-backed append-only shared environment for swarm artifacts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from .schema import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    EvaluationRecord,
    SchemaError,
    canonical_hash,
    parse_timestamp,
    utc_now,
)


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WorkerState:
    worker_id: str
    model: str
    capabilities: tuple[str, ...]
    virtual_budget: float
    behavioral_pattern: str | None
    created_at: str


class ArtifactStore:
    """Durable store whose scientific ledgers cannot be updated or deleted.

    Worker budget is materialized mutable state backed by append-only budget
    events.  Artifacts, lineage, evaluations, goal feedback, and purchases are
    protected by SQLite triggers.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evaluation_at TEXT,
                    body_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_subject_type_created
                    ON artifacts(subject, artifact_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_artifacts_evaluation_at
                    ON artifacts(artifact_type, evaluation_at);

                CREATE TABLE IF NOT EXISTS artifact_edges (
                    child_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    parent_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    relation TEXT NOT NULL CHECK(relation IN ('parent', 'derived_from')),
                    PRIMARY KEY(child_id, parent_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_edges_parent
                    ON artifact_edges(parent_id, child_id);

                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    prediction_artifact_id TEXT NOT NULL UNIQUE
                        REFERENCES artifacts(artifact_id),
                    evaluated_at TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    virtual_budget REAL NOT NULL CHECK(virtual_budget >= 0),
                    behavioral_pattern TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worker_budget_events (
                    event_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL REFERENCES workers(worker_id),
                    delta REAL NOT NULL,
                    reason TEXT NOT NULL,
                    related_artifact_id TEXT REFERENCES artifacts(artifact_id),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS goal_fitness_events (
                    event_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    delta REAL NOT NULL,
                    resulting_existing_signal REAL NOT NULL,
                    artifact_id TEXT REFERENCES artifacts(artifact_id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_goal_fitness_goal_created
                    ON goal_fitness_events(goal_id, created_at);

                CREATE TABLE IF NOT EXISTS goal_fitness_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    artifact_id TEXT REFERENCES artifacts(artifact_id),
                    body_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_goal_fitness_snapshots_goal_created
                    ON goal_fitness_snapshots(goal_id, created_at);

                CREATE TABLE IF NOT EXISTS purchases (
                    purchase_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    product_level TEXT NOT NULL,
                    amount_usd REAL NOT NULL CHECK(amount_usd >= 0),
                    payment_id TEXT NOT NULL UNIQUE,
                    purchased_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS receipt_reuse_events (
                    reuse_id TEXT PRIMARY KEY,
                    purchase_id TEXT NOT NULL REFERENCES purchases(purchase_id),
                    consumer_ref_hash TEXT NOT NULL,
                    reused_at TEXT NOT NULL
                );
                """
            )
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(row[0]) != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported swarm schema version: {row[0]}")

            for table in (
                "artifacts",
                "artifact_edges",
                "evaluations",
                "worker_budget_events",
                "goal_fitness_events",
                "goal_fitness_snapshots",
                "purchases",
                "receipt_reuse_events",
            ):
                connection.executescript(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS no_update_{table}
                    BEFORE UPDATE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS no_delete_{table}
                    BEFORE DELETE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END;
                    """
                )
            connection.commit()

    def append_artifact(self, artifact: Artifact) -> str:
        artifact.validate()
        body = artifact.to_dict()
        record_hash = artifact.record_hash
        lineage = [
            (parent_id, "parent") for parent_id in artifact.parent_artifacts
        ] + [
            (parent_id, "derived_from") for parent_id in artifact.derived_from
        ]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lineage:
                identifiers = [item[0] for item in lineage]
                placeholders = ",".join("?" for _ in identifiers)
                rows = connection.execute(
                    f"SELECT artifact_id FROM artifacts WHERE artifact_id IN ({placeholders})",
                    identifiers,
                ).fetchall()
                present = {str(row[0]) for row in rows}
                missing = sorted(set(identifiers) - present)
                if missing:
                    raise SchemaError(f"lineage references do not exist: {', '.join(missing)}")
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, created_at, worker_id, artifact_type, subject,
                    status, evaluation_at, body_json, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.created_at,
                    artifact.worker_id,
                    artifact.artifact_type,
                    artifact.subject,
                    artifact.status,
                    artifact.prediction.evaluation_at if artifact.prediction else None,
                    json.dumps(body, ensure_ascii=False, sort_keys=True),
                    record_hash,
                ),
            )
            for parent_id, relation in lineage:
                connection.execute(
                    "INSERT INTO artifact_edges(child_id, parent_id, relation) VALUES (?, ?, ?)",
                    (artifact.artifact_id, parent_id, relation),
                )
            connection.commit()
        return record_hash

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        body = json.loads(str(row["body_json"]))
        artifact = Artifact.from_dict(body)
        if artifact.record_hash != str(row["record_hash"]):
            raise RuntimeError(f"artifact checksum mismatch: {artifact.artifact_id}")
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        return self._artifact_from_row(row) if row else None

    def list_artifacts(
        self,
        *,
        subject: str | None = None,
        artifact_type: str | None = None,
        worker_id: str | None = None,
        limit: int = 1_000,
        newest_first: bool = False,
    ) -> list[Artifact]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("subject", subject),
            ("artifact_type", artifact_type),
            ("worker_id", worker_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "DESC" if newest_first else "ASC"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM artifacts {where} ORDER BY created_at {direction}, artifact_id {direction} LIMIT ?",
                params,
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def lineage(self, artifact_id: str) -> dict[str, list[str]]:
        with self._connection() as connection:
            ancestors = connection.execute(
                """
                WITH RECURSIVE lineage(id) AS (
                    SELECT parent_id FROM artifact_edges WHERE child_id = ?
                    UNION
                    SELECT e.parent_id FROM artifact_edges e JOIN lineage l ON e.child_id = l.id
                ) SELECT id FROM lineage ORDER BY id
                """,
                (artifact_id,),
            ).fetchall()
            descendants = connection.execute(
                """
                WITH RECURSIVE lineage(id) AS (
                    SELECT child_id FROM artifact_edges WHERE parent_id = ?
                    UNION
                    SELECT e.child_id FROM artifact_edges e JOIN lineage l ON e.parent_id = l.id
                ) SELECT id FROM lineage ORDER BY id
                """,
                (artifact_id,),
            ).fetchall()
        return {
            "ancestors": [str(row[0]) for row in ancestors],
            "descendants": [str(row[0]) for row in descendants],
        }

    def due_predictions(self, now: str) -> list[Artifact]:
        now_dt = parse_timestamp(now, "now")
        candidates = self.list_artifacts(artifact_type=ArtifactType.PREDICTION.value, limit=10_000)
        evaluated = {
            row[0]
            for row in self._query("SELECT prediction_artifact_id FROM evaluations")
        }
        return [
            artifact
            for artifact in candidates
            if artifact.artifact_id not in evaluated
            and artifact.status
            not in {
                ArtifactStatus.INVALIDATED.value,
                ArtifactStatus.REJECTED.value,
                ArtifactStatus.FAILED.value,
            }
            and artifact.prediction is not None
            and parse_timestamp(artifact.prediction.evaluation_at, "evaluation_at") <= now_dt
        ]

    def append_evaluation(self, record: EvaluationRecord) -> str:
        record.validate()
        prediction = self.get_artifact(record.prediction_artifact_id)
        if prediction is None or prediction.artifact_type != ArtifactType.PREDICTION.value:
            raise SchemaError("evaluation must reference a prediction artifact")
        body = record.to_dict()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO evaluations(
                    evaluation_id, prediction_artifact_id, evaluated_at,
                    body_json, record_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.evaluation_id,
                    record.prediction_artifact_id,
                    record.evaluated_at,
                    json.dumps(body, ensure_ascii=False, sort_keys=True),
                    record.record_hash,
                ),
            )
            connection.commit()
        return record.record_hash

    def evaluations(self) -> list[EvaluationRecord]:
        rows = self._query("SELECT body_json, record_hash FROM evaluations ORDER BY evaluated_at")
        output: list[EvaluationRecord] = []
        for row in rows:
            data = json.loads(str(row["body_json"]))
            record = EvaluationRecord(**data)
            record.validate()
            if record.record_hash != str(row["record_hash"]):
                raise RuntimeError(f"evaluation checksum mismatch: {record.evaluation_id}")
            output.append(record)
        return output

    def evaluation_for(self, prediction_artifact_id: str) -> EvaluationRecord | None:
        rows = self._query(
            "SELECT body_json, record_hash FROM evaluations WHERE prediction_artifact_id = ?",
            (prediction_artifact_id,),
        )
        if not rows:
            return None
        data = json.loads(str(rows[0]["body_json"]))
        record = EvaluationRecord(**data)
        record.validate()
        if record.record_hash != str(rows[0]["record_hash"]):
            raise RuntimeError(f"evaluation checksum mismatch: {record.evaluation_id}")
        return record

    def register_workers(
        self,
        *,
        count: int = 50,
        model: str = "gpt-5.6-luna",
        capabilities: Sequence[str],
        initial_budget: float = 100.0,
    ) -> list[str]:
        if count < 1 or count > 1_000:
            raise ValueError("worker count must be between 1 and 1000")
        if initial_budget < 0:
            raise ValueError("initial_budget cannot be negative")
        normalized = tuple(sorted({item.strip() for item in capabilities if item.strip()}))
        if not normalized:
            raise ValueError("at least one common capability is required")
        created_at = utc_now()
        worker_ids = [f"luna-{index:03d}" for index in range(1, count + 1)]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for worker_id in worker_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO workers(
                        worker_id, model, capabilities_json, virtual_budget,
                        behavioral_pattern, created_at
                    ) VALUES (?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        worker_id,
                        model,
                        json.dumps(normalized, ensure_ascii=False),
                        initial_budget,
                        created_at,
                    ),
                )
            connection.commit()
        return worker_ids

    def workers(self) -> list[WorkerState]:
        rows = self._query("SELECT * FROM workers ORDER BY worker_id")
        return [
            WorkerState(
                worker_id=str(row["worker_id"]),
                model=str(row["model"]),
                capabilities=tuple(json.loads(str(row["capabilities_json"]))),
                virtual_budget=float(row["virtual_budget"]),
                behavioral_pattern=row["behavioral_pattern"],
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def set_behavioral_pattern(self, worker_id: str, pattern: str | None) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE workers SET behavioral_pattern = ? WHERE worker_id = ?",
                (pattern, worker_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(worker_id)
            connection.commit()

    def append_budget_event(
        self,
        *,
        worker_id: str,
        delta: float,
        reason: str,
        related_artifact_id: str | None = None,
        event_id: str | None = None,
    ) -> float:
        if not reason.strip():
            raise ValueError("budget event reason is required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT virtual_budget FROM workers WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            if row is None:
                raise KeyError(worker_id)
            current_budget = float(row[0])
            next_budget = max(0.0, current_budget + float(delta))
            applied_delta = next_budget - current_budget
            connection.execute(
                """
                INSERT INTO worker_budget_events(
                    event_id, worker_id, delta, reason, related_artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id or f"budget_{uuid.uuid4().hex}",
                    worker_id,
                    applied_delta,
                    reason,
                    related_artifact_id,
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE workers SET virtual_budget = ? WHERE worker_id = ?",
                (next_budget, worker_id),
            )
            connection.commit()
        return next_budget

    def append_goal_fitness_event(
        self,
        *,
        goal_id: str,
        event_name: str,
        delta: float,
        resulting_existing_signal: float,
        artifact_id: str | None,
    ) -> str:
        event_id = f"goalfit_{uuid.uuid4().hex}"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO goal_fitness_events(
                    event_id, goal_id, event_name, delta,
                    resulting_existing_signal, artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    goal_id,
                    event_name,
                    float(delta),
                    float(resulting_existing_signal),
                    artifact_id,
                    utc_now(),
                ),
            )
            connection.commit()
        return event_id

    def current_existing_signal(self, goal_id: str) -> float:
        rows = self._query(
            """
            SELECT resulting_existing_signal FROM goal_fitness_events
            WHERE goal_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (goal_id,),
        )
        return float(rows[0][0]) if rows else 0.0

    def goal_event_count(self, goal_id: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS count FROM goal_fitness_events WHERE goal_id = ?",
            (goal_id,),
        )
        return int(rows[0]["count"])

    def append_goal_fitness_snapshot(
        self,
        *,
        goal_id: str,
        fitness_payload: dict[str, Any],
        artifact_id: str | None = None,
    ) -> str:
        if not goal_id.strip():
            raise ValueError("goal_id is required")
        if artifact_id is not None and self.get_artifact(artifact_id) is None:
            raise KeyError(artifact_id)
        created_at = utc_now()
        snapshot_id = f"goalfitness_{uuid.uuid4().hex}"
        body = {
            "snapshot_id": snapshot_id,
            "goal_id": goal_id,
            "artifact_id": artifact_id,
            "fitness": fitness_payload,
            "created_at": created_at,
        }
        record_hash = canonical_hash(body)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO goal_fitness_snapshots(
                    snapshot_id, goal_id, artifact_id, body_json,
                    record_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    goal_id,
                    artifact_id,
                    json.dumps(body, ensure_ascii=False, sort_keys=True),
                    record_hash,
                    created_at,
                ),
            )
            connection.commit()
        return snapshot_id

    def latest_goal_fitness(self, goal_id: str) -> dict[str, Any] | None:
        rows = self._query(
            """
            SELECT body_json, record_hash FROM goal_fitness_snapshots
            WHERE goal_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (goal_id,),
        )
        if not rows:
            return None
        body = json.loads(str(rows[0]["body_json"]))
        if canonical_hash(body) != str(rows[0]["record_hash"]):
            raise RuntimeError(f"goal fitness checksum mismatch: {goal_id}")
        return body

    def last_goal_artifact(self, goal_id: str) -> str | None:
        rows = self._query(
            """
            SELECT artifact_id FROM goal_fitness_events
            WHERE goal_id = ? AND artifact_id IS NOT NULL
            ORDER BY rowid DESC LIMIT 1
            """,
            (goal_id,),
        )
        return str(rows[0][0]) if rows else None

    def append_purchase(
        self,
        *,
        artifact_id: str,
        product_level: str,
        amount_usd: float,
        payment_id: str,
        receipt_payload: dict[str, Any],
        purchase_id: str | None = None,
    ) -> str:
        if self.get_artifact(artifact_id) is None:
            raise KeyError(artifact_id)
        if amount_usd < 0:
            raise ValueError("amount_usd cannot be negative")
        receipt_hash = canonical_hash(receipt_payload)
        purchase_id = purchase_id or f"purchase_{uuid.uuid4().hex}"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO purchases(
                    purchase_id, artifact_id, product_level, amount_usd,
                    payment_id, purchased_at, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase_id,
                    artifact_id,
                    product_level,
                    float(amount_usd),
                    payment_id,
                    utc_now(),
                    receipt_hash,
                    json.dumps(receipt_payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
        return purchase_id

    def purchase_by_payment_id(self, payment_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT * FROM purchases WHERE payment_id = ?", (payment_id,)
        )
        if not rows:
            return None
        row = rows[0]
        receipt = json.loads(str(row["receipt_json"]))
        if canonical_hash(receipt) != str(row["receipt_hash"]):
            raise RuntimeError(f"purchase receipt checksum mismatch: {row['purchase_id']}")
        return {
            "purchase_id": str(row["purchase_id"]),
            "artifact_id": str(row["artifact_id"]),
            "product_level": str(row["product_level"]),
            "amount_usd": float(row["amount_usd"]),
            "payment_id": str(row["payment_id"]),
            "purchased_at": str(row["purchased_at"]),
            "receipt": receipt,
        }

    def record_receipt_reuse(self, purchase_id: str, consumer_ref: str) -> str:
        if not consumer_ref.strip():
            raise ValueError("consumer_ref is required")
        reuse_id = f"reuse_{uuid.uuid4().hex}"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO receipt_reuse_events(
                    reuse_id, purchase_id, consumer_ref_hash, reused_at
                ) VALUES (?, ?, ?, ?)
                """,
                (reuse_id, purchase_id, canonical_hash(consumer_ref), utc_now()),
            )
            connection.commit()
        return reuse_id

    def purchase_count(self, artifact_id: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS count FROM purchases WHERE artifact_id = ?", (artifact_id,)
        )
        return int(rows[0]["count"])

    def reuse_count(self, artifact_id: str) -> int:
        rows = self._query(
            "SELECT COUNT(DISTINCT child_id) AS count FROM artifact_edges WHERE parent_id = ?",
            (artifact_id,),
        )
        return int(rows[0]["count"])

    def summary(self) -> dict[str, Any]:
        counts = {
            str(row["artifact_type"]): int(row["count"])
            for row in self._query(
                "SELECT artifact_type, COUNT(*) AS count FROM artifacts GROUP BY artifact_type"
            )
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact_count": sum(counts.values()),
            "artifacts_by_type": counts,
            "evaluation_count": int(self._query("SELECT COUNT(*) FROM evaluations")[0][0]),
            "worker_count": int(self._query("SELECT COUNT(*) FROM workers")[0][0]),
            "goal_fitness_snapshot_count": int(
                self._query("SELECT COUNT(*) FROM goal_fitness_snapshots")[0][0]
            ),
            "purchase_count": int(self._query("SELECT COUNT(*) FROM purchases")[0][0]),
        }

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(connection.execute(sql, tuple(params)).fetchall())
