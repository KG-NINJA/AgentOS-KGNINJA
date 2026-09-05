#!/usr/bin/env python3
"""Local code-generation preflight. No model, network, shell or finance call.

Run from the generator before its existing Codex invocation. Source snapshots and
receipts are local evidence only, never authority or proof of model availability.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
KERNEL_SCRIPTS = REPOSITORY / ".agents/skills/gpt6-work-platform/scripts"
sys.path.insert(0, str(KERNEL_SCRIPTS))
import work_kernel as kernel

FINANCE_FLAGS = ("swarm_real_money_enabled", "swarm_paper_trading_enabled",
                 "swarm_x402_settlement_enabled", "swarm_real_execution_enabled")


def private_store(root: Path) -> Path:
    """Operator-owned paths only; reject symlinks before opening a private DB."""
    directory = root
    for component in ("runtime", "work-platform"):
        directory = directory / component
        if directory.is_symlink():
            raise kernel.Rejected("symlink runtime directory")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise kernel.Rejected("invalid runtime directory")
    os.chmod(directory, 0o700)
    path = directory / "evidence.sqlite3"
    if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.stat().st_mode)):
        raise kernel.Rejected("invalid evidence database")
    return path


async def prepare(root: Path, run_id: str, project: str, expected_spec: bytes) -> dict:
    root = root.resolve(strict=True)
    kernel.identifier(run_id)
    if not re.fullmatch(r"workspace/project-[0-9]{3,}", project):
        raise kernel.Rejected("target outside generated projects")
    expected = kernel.parse_json(expected_spec)
    if type(expected) is not dict:
        raise kernel.Rejected("spec must be an object")
    paths = ("AGENTS.md", "config.json", project + "/docs/SPEC.json")
    # Read once. Arguments bind the exact snapshot bytes, not mutable filenames.
    snapshots = {path: kernel.read_scoped(root, path) for path in paths}
    config = kernel.parse_json(snapshots["config.json"])
    if type(config) is not dict or any(config.get(flag) is not False for flag in FINANCE_FLAGS):
        raise kernel.Rejected("current preflight requires closed finance gates")
    if kernel.canonical(kernel.parse_json(snapshots[paths[2]])) != kernel.canonical(expected):
        raise kernel.Rejected("spec snapshot differs from prompt input")
    if len(snapshots[paths[0]]) > 8192:
        raise kernel.Rejected("root instructions exceed 8 KiB")
    pins = {path: hashlib.sha256(raw).hexdigest() for path, raw in snapshots.items()}
    def validate(args: dict) -> None:
        if set(args) != {"path", "sha256"} or args.get("path") not in pins or args["sha256"] != pins[args["path"]]:
            raise kernel.Rejected("source not registered")
    async def read(args: dict, deps: dict) -> dict:
        return {"data": {"text": snapshots[args["path"]].decode("utf-8")},
                "source_refs": ["local-file:" + args["path"]],
                "observed_at": time.time(), "warnings": []}
    registry = {"local.snapshot": kernel.ReadOperation("preflight-v1", validate, read)}
    tasks = [{"id": label, "operation": "local.snapshot", "depends_on": [],
              "args": {"path": path, "sha256": pins[path]}}
             for label, path in zip(("instructions", "config", "spec"), paths)]
    path = private_store(root)
    store = kernel.EvidenceStore(path)
    try:
        os.chmod(path, 0o600)
        receipt = await kernel.run_reads(run_id, tasks, registry, ("local.snapshot",), store)
        if receipt["status"] != "ok":
            raise kernel.Rejected("incomplete preflight")
        return {"preflight": "verified_local_snapshot", "run_id": run_id,
                "plan_sha256": receipt["plan_hash"], "spec_sha256": pins[paths[2]],
                "evidence_store": "runtime/work-platform/evidence.sqlite3",
                "source_count": 3, "model_access_verified": False,
                "financial_authority": False}
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        data = sys.stdin.buffer.read(kernel.MAX_BYTES + 1)
        result = asyncio.run(prepare(args.root, args.run_id, args.project, data))
        print(kernel.canonical(result).decode("utf-8"))
        return 0
    except Exception as exc:
        # Do not emit raw specs, absolute paths, errors or potential credentials.
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}), file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
