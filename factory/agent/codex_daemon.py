#!/usr/bin/env python3
"""Repair daemon: lease repair requests and execute fixes through codex app-server."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RpcResponse:
    result: Any | None = None
    error: Any | None = None


class AppServerClient:
    def __init__(self, root: Path, listen: str = "stdio://") -> None:
        self.root = root
        self.listen = listen
        self.proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._pending: dict[int, queue.Queue[RpcResponse]] = {}
        self._id = 0
        self._lock = threading.Lock()
        self._logs = root / "runtime" / "repair_queue" / "logs"
        self._logs.mkdir(parents=True, exist_ok=True)
        self._log_path = self._logs / "daemon_app_server.log"

    def start(self) -> None:
        cmd = ["codex", "app-server", "--listen", self.listen]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.request(
            "initialize",
            {"clientInfo": {"name": "kg-autonomous-codex-daemon", "version": "1.0.0"}},
            timeout=20,
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                resp_id = msg.get("id")
                q = self._pending.pop(resp_id, None)
                if q:
                    q.put(RpcResponse(result=msg.get("result"), error=msg.get("error")))

    def request(self, method: str, params: dict[str, Any], timeout: int = 300) -> Any:
        assert self.proc and self.proc.stdin
        req_id = self._next_id()
        q: queue.Queue[RpcResponse] = queue.Queue(maxsize=1)
        self._pending[req_id] = q
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty as exc:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"app-server timeout method={method}") from exc
        if resp.error is not None:
            raise RuntimeError(f"app-server error method={method} error={resp.error}")
        return resp.result


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="codex repair daemon")
    p.add_argument("--root", default=".", help="kg-autonomous root")
    p.add_argument("--listen", default="stdio://", help="app-server listen endpoint")
    p.add_argument("--poll-sec", type=float, default=1.0)
    p.add_argument("--approval-policy", default=os.environ.get("APPROVAL_POLICY", "untrusted"))
    p.add_argument("--sandbox-mode", default=os.environ.get("SANDBOX_MODE", "workspace-write"))
    p.add_argument("--repair-timeout", type=int, default=900)
    return p.parse_args()


def lease_one(incoming: Path, leased: Path, lock_path: Path) -> Path | None:
    leased.mkdir(parents=True, exist_ok=True)
    incoming.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        import fcntl

        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        items = sorted(incoming.glob("*.json"))
        if not items:
            return None
        src = items[0]
        dst = leased / src.name
        if dst.exists():
            dst = leased / f"{src.stem}_{utc_now().replace(':', '').replace('-', '')}.json"
        src.rename(dst)
        return dst


def ensure_safe_target(req: dict[str, Any]) -> tuple[bool, str]:
    target = Path(req.get("target_dir", "")).resolve()
    constraints = req.get("constraints") or {}
    allow_write_root = Path(constraints.get("allow_write_root") or target).resolve()
    forbid_paths = [Path(p).resolve() for p in (constraints.get("forbid_paths") or [])]

    if not str(target).startswith(str(allow_write_root)):
        return False, f"target_outside_allow_root:{target}"
    for p in forbid_paths:
        if str(target).startswith(str(p)):
            return False, f"target_in_forbid_path:{target}"
    return True, ""


def build_prompt(req: dict[str, Any], validate_tail: str) -> str:
    return (
        "You are a repair-only agent.\n\n"
        "Task:\n"
        "- Fix failing validation in this project directory.\n\n"
        "Strict constraints:\n"
        "1) Apply the minimum diff required to pass validation.\n"
        "2) Do NOT modify files outside this working directory.\n"
        "3) Do NOT regenerate the whole project.\n"
        "4) Keep existing architecture and file layout.\n"
        "5) If uncertain, prefer tiny, conservative fixes.\n\n"
        f"Job ID: {req.get('job_id')}\n"
        f"Target dir: {req.get('target_dir')}\n\n"
        "Validation failure context:\n"
        f"{validate_tail}\n"
    )


def run_repair(
    app: AppServerClient,
    req: dict[str, Any],
    approval_policy: str,
    sandbox_mode: str,
    repair_timeout: int,
) -> tuple[bool, str, dict[str, Any]]:
    if os.environ.get("REPAIR_DAEMON_MOCK", "0") == "1":
        return True, "", {"stdout": "mock repair success", "stderr": "", "exit_code": 0}

    ok, reason = ensure_safe_target(req)
    if not ok:
        return False, reason, {}

    validate_log = Path(req.get("validate_log_path", ""))
    validate_tail = "(missing validate log)"
    if validate_log.exists():
        lines = validate_log.read_text(encoding="utf-8", errors="ignore").splitlines()
        validate_tail = "\n".join(lines[-200:])

    prompt = build_prompt(req, validate_tail)
    target_dir = str(Path(req["target_dir"]).resolve())
    command = [
        "codex",
        "exec",
        "--sandbox",
        sandbox_mode,
        "-a",
        approval_policy,
        "-C",
        target_dir,
        prompt,
    ]

    try:
        res = app.request(
            "command/exec",
            {"command": command, "cwd": target_dir, "timeoutMs": repair_timeout * 1000},
            timeout=repair_timeout + 60,
        )
    except TimeoutError:
        return False, "timeout", {}
    except Exception as exc:
        return False, f"protocol:{exc}", {}

    exit_code = int((res or {}).get("exitCode", 1))
    meta = {
        "stdout": (res or {}).get("stdout", "")[-4000:],
        "stderr": (res or {}).get("stderr", "")[-4000:],
        "exit_code": exit_code,
    }
    if exit_code == 0:
        return True, "", meta
    return False, "repair-failed", meta


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    rq = root / "runtime" / "repair_queue"
    incoming = rq / "incoming"
    leased = rq / "leased"
    done = rq / "done"
    failed = rq / "failed"
    results = rq / "results"
    logs = rq / "logs"
    pid_file = rq / "daemon.pid"
    for d in (incoming, leased, done, failed, results, logs):
        d.mkdir(parents=True, exist_ok=True)

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    app = AppServerClient(root=root, listen=args.listen)
    app.start()

    log_path = logs / "daemon.log"
    try:
        while True:
            leased_file = lease_one(incoming, leased, rq / ".lease.lock")
            if leased_file is None:
                time.sleep(args.poll_sec)
                continue
            req_id = leased_file.stem
            try:
                req = load_json(leased_file)
                req_job_id = str(req.get("job_id") or req_id)
                success, reason, meta = run_repair(
                    app=app,
                    req=req,
                    approval_policy=args.approval_policy,
                    sandbox_mode=args.sandbox_mode,
                    repair_timeout=args.repair_timeout,
                )
                result = {
                    "job_id": req_job_id,
                    "request_id": req_id,
                    "status": "ok" if success else "failed",
                    "fail_reason": reason or None,
                    "updated_at": utc_now(),
                }
                result.update(meta)
                write_json(results / f"{req_id}.json", result)
                if success:
                    leased_file.rename(done / leased_file.name)
                else:
                    leased_file.rename(failed / leased_file.name)
            except Exception as exc:
                err = {"job_id": req_id, "status": "failed", "fail_reason": f"daemon:{exc}", "updated_at": utc_now()}
                write_json(results / f"{req_id}.json", err)
                try:
                    leased_file.rename(failed / leased_file.name)
                except Exception:
                    pass
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{utc_now()}] processed {leased_file.name}\n")
    finally:
        app.stop()
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
