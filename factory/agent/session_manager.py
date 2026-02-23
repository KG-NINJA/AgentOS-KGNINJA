#!/usr/bin/env python3
"""Persistent session registry for per-app Codex sessions."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class SessionManager:
    def __init__(self, root: Path, max_idle_sec: int = 86400) -> None:
        self.root = root
        self.max_idle_sec = max_idle_sec
        self.runtime_dir = self.root / "runtime"
        self.path = self.runtime_dir / "codex_sessions.json"
        self.lock_path = self.runtime_dir / "codex_sessions.lock"
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_atomic({})

    def _read(self) -> dict[str, Any]:
        self._ensure_file()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="codex_sessions.", suffix=".tmp", dir=str(self.runtime_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = self.lock_path.open("a+", encoding="utf-8")
        import fcntl

        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        return lock_fh

    def load_sessions(self) -> dict[str, Any]:
        lock_fh = self._locked()
        try:
            return self._read()
        finally:
            lock_fh.close()

    def save_sessions(self, sessions: dict[str, Any]) -> None:
        lock_fh = self._locked()
        try:
            self._write_atomic(sessions)
        finally:
            lock_fh.close()

    def get_session(self, app_id: str) -> str | None:
        now = int(time.time())
        lock_fh = self._locked()
        try:
            sessions = self._read()
            rec = sessions.get(app_id)
            if not isinstance(rec, dict):
                return None
            sid = rec.get("session_id")
            last_used = int(rec.get("last_used", 0) or 0)
            if not sid:
                return None
            if self.max_idle_sec > 0 and last_used > 0 and now - last_used > self.max_idle_sec:
                sessions.pop(app_id, None)
                self._write_atomic(sessions)
                return None
            return str(sid)
        finally:
            lock_fh.close()

    def set_session(self, app_id: str, session_id: str) -> None:
        now = int(time.time())
        lock_fh = self._locked()
        try:
            sessions = self._read()
            rec = sessions.get(app_id)
            created = now
            if isinstance(rec, dict) and rec.get("created_at"):
                created = int(rec.get("created_at") or now)
            sessions[app_id] = {
                "session_id": session_id,
                "created_at": created,
                "last_used": now,
            }
            self._write_atomic(sessions)
        finally:
            lock_fh.close()

    def update_last_used(self, app_id: str) -> None:
        now = int(time.time())
        lock_fh = self._locked()
        try:
            sessions = self._read()
            rec = sessions.get(app_id)
            if not isinstance(rec, dict):
                return
            rec["last_used"] = now
            sessions[app_id] = rec
            self._write_atomic(sessions)
        finally:
            lock_fh.close()

    def count_active_sessions(self) -> int:
        now = int(time.time())
        lock_fh = self._locked()
        try:
            sessions = self._read()
            changed = False
            active = 0
            for app_id in list(sessions.keys()):
                rec = sessions.get(app_id)
                if not isinstance(rec, dict) or not rec.get("session_id"):
                    sessions.pop(app_id, None)
                    changed = True
                    continue
                last_used = int(rec.get("last_used", 0) or 0)
                if self.max_idle_sec > 0 and last_used > 0 and now - last_used > self.max_idle_sec:
                    sessions.pop(app_id, None)
                    changed = True
                    continue
                active += 1
            if changed:
                self._write_atomic(sessions)
            return active
        finally:
            lock_fh.close()


def _manager(root: str | Path = ".", max_idle_sec: int | None = None) -> SessionManager:
    idle = max_idle_sec if max_idle_sec is not None else int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400"))
    return SessionManager(Path(root).resolve(), max_idle_sec=idle)


def load_sessions(root: str | Path = ".", max_idle_sec: int | None = None) -> dict[str, Any]:
    return _manager(root, max_idle_sec).load_sessions()


def save_sessions(sessions: dict[str, Any], root: str | Path = ".", max_idle_sec: int | None = None) -> None:
    _manager(root, max_idle_sec).save_sessions(sessions)


def get_session(app_id: str, root: str | Path = ".", max_idle_sec: int | None = None) -> str | None:
    return _manager(root, max_idle_sec).get_session(app_id)


def set_session(app_id: str, session_id: str, root: str | Path = ".", max_idle_sec: int | None = None) -> None:
    _manager(root, max_idle_sec).set_session(app_id, session_id)


def update_last_used(app_id: str, root: str | Path = ".", max_idle_sec: int | None = None) -> None:
    _manager(root, max_idle_sec).update_last_used(app_id)
