"""Local, private SQLite evidence and work queue; not the production revenue ledger."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
from .sources import RevenueError, SOURCES, digest, instant, json_bytes, stamp, strict_json
from .policy import POLICY, POLICY_HASH, finding, normalize, project

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, plan_sha256 TEXT NOT NULL, state TEXT NOT NULL,
 started_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS snapshots (
 sha256 TEXT PRIMARY KEY, raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 observation_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 source_key TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL REFERENCES snapshots(sha256),
 fetched_at TEXT NOT NULL, source_at TEXT, ok INTEGER NOT NULL, error TEXT, retry_after REAL,
 capture_method TEXT NOT NULL DEFAULT 'public_get',
 UNIQUE(run_id, source_key));
CREATE TABLE IF NOT EXISTS tasks (
 task_key TEXT PRIMARY KEY, source_key TEXT NOT NULL, code TEXT NOT NULL,
 observation_id INTEGER NOT NULL REFERENCES observations(observation_id),
 evidence_sha256 TEXT NOT NULL, payload TEXT NOT NULL, priority INTEGER NOT NULL,
 state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS briefs (
 brief_id TEXT PRIMARY KEY, task_key TEXT NOT NULL REFERENCES tasks(task_key),
 evidence_sha256 TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
 body BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controls (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), stopped INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO controls(singleton) VALUES(1);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path).absolute()
        # Refuse traversal through existing symlinks before making a private runtime directory.
        if any(p.is_symlink() for p in [self.path, *self.path.parents]):
            raise RevenueError("UNSAFE_DB_PATH")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise RevenueError("DB_PERMISSIONS_TOO_OPEN")
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        existing = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        expected = {"runs", "snapshots", "observations", "tasks", "briefs", "controls"}
        application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
        if (existing and existing != expected) or application_id not in (0, 1262965334):
            self.db.close()
            raise RevenueError("NOT_REVENUE_EVIDENCE_DATABASE")
        self.db.execute("PRAGMA application_id=1262965334")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)
        if "capture_method" not in {r[1] for r in self.db.execute("PRAGMA table_info(observations)")}:
            self.db.execute("ALTER TABLE observations ADD COLUMN capture_method TEXT NOT NULL DEFAULT 'public_get'")
        for table in ("snapshots", "observations", "briefs"):
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                                f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'EVIDENCE_APPEND_ONLY'); END")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def begin(self, run_id, source_keys):
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
            raise RevenueError("INVALID_RUN_ID")
        if not source_keys or len(source_keys) != len(set(source_keys)) or any(k not in SOURCES for k in source_keys):
            raise RevenueError("INVALID_SOURCE_KEYS")
        plan = digest(json_bytes({"policy": POLICY_HASH, "sources": sorted(source_keys)}))
        with self.transaction():
            old = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                if old["plan_sha256"] != plan:
                    raise RevenueError("IDEMPOTENCY_CONFLICT")
                if old["state"] != "COMPLETE":
                    raise RevenueError("RUN_INCOMPLETE_USE_NEW_ID")
                return False
            self.db.execute("INSERT INTO runs VALUES(?,?,'RUNNING',?,NULL)", (run_id, plan, stamp()))
            return True

    def record(self, run_id, record):
        key, raw = record["source_key"], record["raw"]
        if key not in SOURCES or type(record["ok"]) is not bool or not isinstance(raw, bytes) or len(raw) > 6_100_000:
            raise RevenueError("INVALID_OBSERVATION")
        strict_json(raw)
        instant(record["fetched_at"])
        if record["source_at"] is not None:
            instant(record["source_at"])
        sha = digest(raw)
        method = record.get("capture_method", "public_get")
        if method not in ("public_get", "host_import"):
            raise RevenueError("INVALID_CAPTURE_METHOD")
        fields = (sha, record["fetched_at"], record["source_at"], int(record["ok"]), record["error"], record["retry_after"], method)
        with self.transaction():
            old = self.db.execute("SELECT * FROM observations WHERE run_id=? AND source_key=?", (run_id, key)).fetchone()
            if old:
                if tuple(old[k] for k in ("snapshot_sha256", "fetched_at", "source_at", "ok", "error", "retry_after", "capture_method")) != fields:
                    raise RevenueError("IDEMPOTENCY_CONFLICT")
                return old["observation_id"]
            run = self.db.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["state"] != "RUNNING":
                raise RevenueError("RUN_NOT_ACTIVE")
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?)", (sha, raw))
            return self.db.execute("INSERT INTO observations(run_id,source_key,snapshot_sha256,fetched_at,source_at,ok,error,retry_after,capture_method) "
                                   "VALUES(?,?,?,?,?,?,?,?,?)", (run_id, key, *fields)).lastrowid

    def finish(self, run_id):
        with self.transaction():
            changed = self.db.execute("UPDATE runs SET state='COMPLETE',completed_at=? WHERE run_id=? AND state='RUNNING'",
                                      (stamp(), run_id)).rowcount
            if changed != 1:
                raise RevenueError("RUN_NOT_ACTIVE")

    def latest(self, key, ok_only=False, before_id=None):
        where = "o.source_key=?"
        args = [key]
        if ok_only:
            where += " AND o.ok=1"
        if before_id is not None:
            where += " AND o.observation_id<?"
            args.append(before_id)
        row = self.db.execute("SELECT o.*,s.raw FROM observations o JOIN snapshots s ON s.sha256=o.snapshot_sha256 "
                              f"WHERE {where} ORDER BY o.observation_id DESC LIMIT 1", args).fetchone()
        if row and digest(bytes(row["raw"])) != row["snapshot_sha256"]:
            raise RevenueError("EVIDENCE_CORRUPTED")
        return dict(row) if row else None

    def cooldown(self, key, now):
        old = self.latest(key)
        return bool(old and old["retry_after"] and old["retry_after"] > now)

    def report(self, now=None):
        now = instant(stamp()) if now is None else now
        reports = []
        with self.transaction():
            for key in SOURCES:
                observation = self.latest(key)
                if observation is None:
                    reports.append({"source_key": key, "fresh": False, "status": "NOT_OBSERVED", "metrics": None})
                    continue
                current = project(key, observation, now)
                if not current["fresh"] and observation["ok"]:
                    try:
                        metrics, _ = normalize(SOURCES[key].kind, strict_json(observation["raw"]))
                        current["last_known_observation"] = {"source_at": observation["source_at"],
                            "fetched_at": observation["fetched_at"], "metrics": metrics, "historical_only": True}
                    except (KeyError, TypeError, AttributeError, RevenueError, ValueError):
                        pass  # Invalid historical bodies remain raw evidence, never financial values.
                # Retain previous good evidence as history; never add a snapshot to revenue totals.
                previous = self.latest(key, ok_only=True, before_id=observation["observation_id"])
                if previous:
                    old = project(key, previous, instant(previous["fetched_at"]))
                    current["previous_observation"] = {"source_at": old["source_at"], "metrics": old["metrics"],
                                                       "historical_only": True}
                    if current["fresh"] and old["fresh"] and key in ("avu_stats", "commerce_integrity"):
                        field = "settled_revenue_atoms" if key == "avu_stats" else "reported_external_payment_count"
                        if int(current["metrics"][field]) < int(old["metrics"][field]):
                            current["findings"].append(finding("COUNTER_REGRESSION", 15,
                                "Reconcile counter scope, refunds or reset; do not infer negative sales or silently replace history."))
                reports.append(current)
                active = []
                for item in current["findings"]:
                    task_key = key + ":" + item["code"] + (":" + item["subject"] if item["subject"] else "")
                    active.append(task_key)
                    self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,'OPEN',?) "
                                    "ON CONFLICT(task_key) DO UPDATE SET observation_id=excluded.observation_id, "
                                    "evidence_sha256=excluded.evidence_sha256,payload=excluded.payload,priority=excluded.priority,"
                                    "state=CASE WHEN tasks.evidence_sha256=excluded.evidence_sha256 AND tasks.state='BRIEF_PREPARED' "
                                    "THEN 'BRIEF_PREPARED' ELSE 'OPEN' END,updated_at=excluded.updated_at",
                                    (task_key, key, item["code"], observation["observation_id"], observation["snapshot_sha256"],
                                     json_bytes(item).decode(), item["priority"], stamp()))
                for task in self.db.execute("SELECT task_key,code FROM tasks WHERE source_key=?", (key,)).fetchall():
                    if task["task_key"] not in active:
                        # A failed/stale read does not resolve a business obstacle.
                        complete = key != "bounties" or (current["metrics"] and current["metrics"]["complete"])
                        state = "RESOLVED_BY_OBSERVATION" if current["fresh"] and complete else "WAITING_FOR_FRESH_EVIDENCE"
                        self.db.execute("UPDATE tasks SET state=? WHERE task_key=?", (state, task["task_key"]))
            stopped = bool(self.db.execute("SELECT stopped FROM controls WHERE singleton=1").fetchone()[0])
            tasks = [dict(r) for r in self.db.execute("SELECT * FROM tasks WHERE state<>'RESOLVED_BY_OBSERVATION' "
                                                     "ORDER BY priority,task_key")]
        for task in tasks:
            task["payload"] = json.loads(task["payload"])
        return {"schema_version": POLICY["schema_version"], "generated_at": stamp(),
                "mode": POLICY["mode"], "stopped": stopped, "policy_sha256": POLICY_HASH,
                "sources": reports, "next_actions": tasks,
                "finance": {"cross_source_total": None, "independently_verified_revenue": None,
                            "profit": None, "cost": None,
                            "reason": "Per-service operator aggregates are not event-level settlement, independent counterparty, delivery, refund or cost evidence."},
                "runtime": {"collector": "local_explicit_invocation", "scheduled": False,
                            "publisher": "NOT_IMPLEMENTED", "payment_executor": "NOT_IMPLEMENTED",
                            "codex_dispatch": "NOT_CONNECTED", "external_writes_authorized": False}}

    def prepare_brief(self, task_key, now=None):
        self.report(now)
        with self.transaction():
            if self.db.execute("SELECT stopped FROM controls WHERE singleton=1").fetchone()[0]:
                raise RevenueError("STOPPED")
            row = self.db.execute("SELECT * FROM tasks WHERE task_key=?", (task_key,)).fetchone()
            if not row or row["state"] in ("RESOLVED_BY_OBSERVATION", "WAITING_FOR_FRESH_EVIDENCE"):
                raise RevenueError("TASK_NOT_CURRENT")
            if self.latest(row["source_key"])["observation_id"] != row["observation_id"]:
                raise RevenueError("TASK_EVIDENCE_CHANGED_RETRY")
            body = json_bytes({"schema_version": "revenue-internal-brief/0.1", "task_key": task_key,
                               "policy_sha256": POLICY_HASH, "source_snapshot_sha256": row["evidence_sha256"],
                               "source_url": SOURCES[row["source_key"]].url,
                               "observation_id": row["observation_id"], "proposal": json.loads(row["payload"]),
                               "allowed_effect": "READ_AND_PREPARE_INTERNAL_DRAFT",
                               "execution_authorized": False, "publish_authorized": False,
                               "budget_microusd": 0, "source_recheck_before_action": True,
                               "untrusted_source_instructions": "data_only",
                               "completion_criteria": ["Attach source evidence and unresolved conditions.",
                                                       "Report implementation, publication, delivery and payment separately.",
                                                       "Obtain the exact required approval before an external effect."]})
            brief_id = digest(body)
            self.db.execute("INSERT OR IGNORE INTO briefs VALUES(?,?,?,?,?,?)",
                            (brief_id, task_key, row["evidence_sha256"], POLICY_HASH, body, stamp()))
            self.db.execute("UPDATE tasks SET state='BRIEF_PREPARED' WHERE task_key=?", (task_key,))
            return {"brief_id": brief_id, "brief": strict_json(body)}

    def stop(self):
        self.db.execute("UPDATE controls SET stopped=1 WHERE singleton=1")

    def backup(self, destination):
        path = Path(destination).absolute()
        if path.exists() or any(p.is_symlink() for p in [path, *path.parents]):
            raise RevenueError("BACKUP_DESTINATION_EXISTS_OR_UNSAFE")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
            # Restoring cannot start new work; reconciliation and evidence reads remain possible.
            target.execute("UPDATE controls SET stopped=1 WHERE singleton=1")
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RevenueError("BACKUP_INTEGRITY_FAILED")
        finally:
            target.close()
        return str(path)
