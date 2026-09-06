from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
from .contracts import require

SCHEMA = """
CREATE TABLE IF NOT EXISTS rc_runtime(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,stop_reason TEXT,revision INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO rc_runtime(id) VALUES(1);
CREATE TABLE IF NOT EXISTS rc_idempotency(scope TEXT,key TEXT,sha TEXT NOT NULL,response BLOB,PRIMARY KEY(scope,key));
CREATE TABLE IF NOT EXISTS rc_opportunities(id TEXT PRIMARY KEY,source_url TEXT NOT NULL,latest_observation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rc_observations(id TEXT PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,opportunity_id TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,observed_at REAL NOT NULL,actor TEXT NOT NULL,synthetic INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rc_proposals(id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL REFERENCES rc_opportunities(id),kind TEXT NOT NULL,payload BLOB NOT NULL,payload_sha TEXT NOT NULL,source_sha TEXT NOT NULL,policy_sha TEXT NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_approvals(id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL REFERENCES rc_proposals(id),owner TEXT NOT NULL,bindings BLOB NOT NULL,expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rc_budgets(id TEXT PRIMARY KEY,limit_cash INTEGER NOT NULL,limit_work INTEGER NOT NULL,limit_human INTEGER NOT NULL,reserved_cash INTEGER NOT NULL DEFAULT 0,reserved_work INTEGER NOT NULL DEFAULT 0,reserved_human INTEGER NOT NULL DEFAULT 0,spent_cash INTEGER NOT NULL DEFAULT 0,spent_work INTEGER NOT NULL DEFAULT 0,spent_human INTEGER NOT NULL DEFAULT 0,basis TEXT NOT NULL,owner TEXT NOT NULL,starts REAL NOT NULL,ends REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_reservations(id TEXT PRIMARY KEY,proposal_id TEXT UNIQUE NOT NULL REFERENCES rc_proposals(id),budget_id TEXT NOT NULL REFERENCES rc_budgets(id),cash INTEGER NOT NULL,work INTEGER NOT NULL,human INTEGER NOT NULL,state TEXT NOT NULL,actual_cash INTEGER,actual_work INTEGER,actual_human INTEGER);
CREATE TABLE IF NOT EXISTS rc_jobs(id TEXT PRIMARY KEY,proposal_id TEXT UNIQUE NOT NULL REFERENCES rc_proposals(id),approval_id TEXT NOT NULL REFERENCES rc_approvals(id),reservation_id TEXT UNIQUE NOT NULL REFERENCES rc_reservations(id),body BLOB NOT NULL,sha TEXT NOT NULL,state TEXT NOT NULL,lease_actor TEXT,lease_until REAL,fence INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,result_sha TEXT,result BLOB,runner_actor TEXT);
CREATE TABLE IF NOT EXISTS rc_artifacts(sha TEXT PRIMARY KEY,body BLOB NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_verifications(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES rc_jobs(id),artifact_sha TEXT NOT NULL REFERENCES rc_artifacts(sha),verifier TEXT NOT NULL,body BLOB NOT NULL,passed INTEGER NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_effects(id TEXT PRIMARY KEY,proposal_id TEXT UNIQUE NOT NULL REFERENCES rc_proposals(id),approval_id TEXT NOT NULL REFERENCES rc_approvals(id),state TEXT NOT NULL,result BLOB,attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rc_effect_events(id INTEGER PRIMARY KEY,effect_id TEXT NOT NULL REFERENCES rc_effects(id),state TEXT NOT NULL,evidence BLOB NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_ledger(event_key TEXT PRIMARY KEY,opportunity_id TEXT,kind TEXT NOT NULL,asset TEXT NOT NULL,amount TEXT NOT NULL,relation TEXT NOT NULL,synthetic INTEGER NOT NULL,related TEXT,transfer_id TEXT,body BLOB NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_delivery(event_key TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL REFERENCES rc_opportunities(id),kind TEXT NOT NULL,body BLOB NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,subject TEXT,code TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rc_cost_events(id TEXT PRIMARY KEY,reservation_id TEXT UNIQUE NOT NULL REFERENCES rc_reservations(id),body BLOB NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path).absolute()
        require(not any(p.is_symlink() for p in [self.path, *self.path.parents]), "UNSAFE_DB_PATH", 400)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        require(not self.path.stat().st_mode & 0o077, "DB_PERMISSIONS_TOO_OPEN", 400)
        self.sql = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.sql.row_factory = sqlite3.Row
        tables = [r[0] for r in self.sql.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        marker = self.sql.execute("PRAGMA application_id").fetchone()[0]
        if (tables and marker != 1262965336) or marker not in (0, 1262965336):
            self.sql.close()
            require(False, "NOT_REVENUE_CONTROLLER_DATABASE", 400)
        self.sql.execute("PRAGMA application_id=1262965336")
        self.sql.execute("PRAGMA foreign_keys=ON")
        self.sql.execute("PRAGMA journal_mode=WAL")
        self.sql.executescript(SCHEMA)
        for name in ("observations", "proposals", "artifacts", "verifications", "effect_events", "ledger", "delivery", "audit", "cost_events"):
            for operation in ("UPDATE", "DELETE"):
                self.sql.execute(f"CREATE TRIGGER IF NOT EXISTS rc_{name}_no_{operation} BEFORE {operation} ON rc_{name} BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")

    @contextmanager
    def atomic(self):
        self.sql.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.sql.execute("COMMIT")
        except BaseException:
            self.sql.execute("ROLLBACK")
            raise

    def one(self, sql, args=()):
        row = self.sql.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.sql.execute(sql, args)]

    def close(self):
        self.sql.close()

    def backup(self, target):
        destination = Path(target).absolute()
        require(not destination.exists() and not any(p.is_symlink() for p in [destination,*destination.parents]), "UNSAFE_BACKUP_PATH", 400)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        other = sqlite3.connect(destination)
        try:
            self.sql.backup(other)
            other.execute("UPDATE rc_runtime SET enabled=0,stop_reason='RESTORE_REVIEW_REQUIRED',revision=revision+1 WHERE id=1")
            # A recorded send might have reached the outside world before the backup.
            other.execute("UPDATE rc_effects SET state='UNKNOWN' WHERE state='SENDING'")
            other.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE id IN (SELECT reservation_id FROM rc_jobs WHERE state IN ('QUEUED','RUNNING','VERIFYING'))")
            other.execute("UPDATE rc_jobs SET state='CANCELLED_ON_RESTORE',fence=fence+1,lease_until=NULL WHERE state IN ('QUEUED','RUNNING','VERIFYING')")
            other.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE proposal_id IN (SELECT proposal_id FROM rc_effects WHERE state='UNKNOWN')")
            other.commit()
            require(other.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_CORRUPTED", 500)
        finally:
            other.close()
        return str(destination)
