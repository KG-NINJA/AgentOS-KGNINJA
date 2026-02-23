#!/usr/bin/env python3
"""Codex app-server client over persistent stdio FIFOs."""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from session_manager import SessionManager


class RpcError(RuntimeError):
    pass


class RpcTimeout(TimeoutError):
    pass


class FifoRpcClient:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime = root / "runtime"
        self.in_fifo = self.runtime / "app_server.stdin"
        self.out_fifo = self.runtime / "app_server.stdout"
        self.lock_file = self.runtime / "app_server.lock"
        self._rid = 0

    def _next_id(self) -> int:
        self._rid += 1
        return self._rid

    def request(self, method: str, params: dict[str, Any], timeout: int) -> Any:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_file.open("a+", encoding="utf-8") as lock_fh:
            import fcntl

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            with self.out_fifo.open("r", encoding="utf-8", buffering=1) as out_fh:
                with self.in_fifo.open("w", encoding="utf-8", buffering=1) as in_fh:
                    req_id = self._next_id()
                    payload = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": method,
                        "params": params,
                    }
                    in_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    in_fh.flush()

                    deadline = time.time() + timeout
                    while time.time() < deadline:
                        remaining = max(0.1, deadline - time.time())
                        ready, _, _ = select.select([out_fh], [], [], remaining)
                        if not ready:
                            continue
                        line = out_fh.readline()
                        if not line:
                            continue
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except Exception:
                            continue
                        if msg.get("id") != req_id:
                            continue
                        if "error" in msg and msg["error"] is not None:
                            raise RpcError(str(msg["error"]))
                        return msg.get("result")
        raise RpcTimeout(f"timeout method={method}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Codex app-server repair client")
    sub = p.add_subparsers(dest="cmd", required=True)

    rep = sub.add_parser("repair", help="Run one repair request")
    rep.add_argument("--root", default=".")
    rep.add_argument("--target-dir", required=True)
    rep.add_argument("--fail-log", default="")
    rep.add_argument("--app-id", default=os.environ.get("APP_ID", "default"))
    rep.add_argument("--timeout", type=int, default=300)
    rep.add_argument("--session-max-idle-sec", type=int, default=int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400")))
    rep.add_argument("--approval-policy", default=os.environ.get("APPROVAL_POLICY", "untrusted"))
    rep.add_argument("--sandbox-mode", default=os.environ.get("SANDBOX_MODE", "workspace-write"))

    st = sub.add_parser("selftest", help="Run one app-server roundtrip test")
    st.add_argument("--root", default=".")
    st.add_argument("--app-id", default="selftest")
    st.add_argument("--timeout", type=int, default=30)
    st.add_argument("--session-max-idle-sec", type=int, default=int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400")))
    st.add_argument("--print-session-id", action="store_true")

    sid = sub.add_parser("session-id", help="Get active session id for app")
    sid.add_argument("--root", default=".")
    sid.add_argument("--app-id", required=True)
    sid.add_argument("--session-max-idle-sec", type=int, default=int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400")))
    return p.parse_args()


def _tail_context(path: str, max_lines: int = 200) -> str:
    if not path:
        return "(no failure log provided)"
    p = Path(path)
    if not p.exists():
        return "(failure log not found)"
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return "(failed to read failure log)"


def _repair_prompt(target_dir: str, context: str, app_id: str, session_id: str) -> str:
    return (
        "You are a repair-only agent.\\n\\n"
        "Task:\\n"
        "- Fix failing validation in this project directory.\\n\\n"
        "Strict constraints:\\n"
        "1) Apply the minimum diff required to pass validation.\\n"
        "2) Do NOT modify files outside this working directory.\\n"
        "3) Do NOT regenerate the whole project.\\n"
        "4) Keep existing architecture and file layout.\\n"
        "5) If uncertain, prefer tiny, conservative fixes.\\n\\n"
        f"App ID: {app_id}\\n"
        f"Session ID: {session_id}\\n"
        f"Target dir: {target_dir}\\n\\n"
        "Validation failure context:\\n"
        f"{context}\\n"
    )


def _resolve_session(sm: SessionManager, app_id: str) -> str:
    sid = sm.get_session(app_id)
    if sid:
        sm.update_last_used(app_id)
        return sid
    sid = str(uuid.uuid4())
    sm.set_session(app_id, sid)
    return sid


def run_repair(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    target = Path(args.target_dir).resolve()
    client = FifoRpcClient(root)
    sm = SessionManager(root=root, max_idle_sec=args.session_max_idle_sec)

    client.request(
        "initialize",
        {"clientInfo": {"name": "kg-autonomous-codex-app-client", "version": "1.0.0"}},
        timeout=min(20, args.timeout),
    )

    session_id = _resolve_session(sm, args.app_id)
    prompt = _repair_prompt(str(target), _tail_context(args.fail_log), args.app_id, session_id)
    command = [
        "codex",
        "exec",
        "--sandbox",
        args.sandbox_mode,
        "-a",
        args.approval_policy,
        "-C",
        str(target),
        prompt,
    ]
    res = client.request(
        "command/exec",
        {
            "command": command,
            "cwd": str(target),
            "timeoutMs": args.timeout * 1000,
        },
        timeout=args.timeout + 60,
    )
    sm.update_last_used(args.app_id)

    exit_code = int((res or {}).get("exitCode", 1))
    if exit_code == 0:
        return 0
    print("fail_reason=codex-repair-failed", file=sys.stderr)
    return 1


def run_selftest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    client = FifoRpcClient(root)
    sm = SessionManager(root=root, max_idle_sec=args.session_max_idle_sec)

    client.request(
        "initialize",
        {"clientInfo": {"name": "kg-autonomous-selftest", "version": "1.0.0"}},
        timeout=min(20, args.timeout),
    )

    session_id = _resolve_session(sm, args.app_id)
    res = client.request(
        "command/exec",
        {
            "command": ["bash", "-lc", "true"],
            "cwd": str(root),
            "timeoutMs": min(args.timeout, 30) * 1000,
        },
        timeout=args.timeout + 10,
    )
    sm.update_last_used(args.app_id)

    exit_code = int((res or {}).get("exitCode", 1))
    if exit_code == 0 and args.print_session_id:
        print(session_id)
    return 0 if exit_code == 0 else 1


def run_session_id(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    sm = SessionManager(root=root, max_idle_sec=args.session_max_idle_sec)
    sid = sm.get_session(args.app_id)
    if not sid:
        return 1
    print(sid)
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.cmd == "repair":
            return run_repair(args)
        if args.cmd == "selftest":
            return run_selftest(args)
        if args.cmd == "session-id":
            return run_session_id(args)
        return 2
    except RpcTimeout:
        print("fail_reason=codex-timeout", file=sys.stderr)
        return 124
    except Exception as exc:
        print(f"fail_reason=codex-app-server-error detail={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
