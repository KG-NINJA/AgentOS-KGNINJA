#!/usr/bin/env python3
"""Small helper to maintain runtime/task_state.json."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"jobs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
            return data
    except Exception:
        pass
    return {"jobs": {}}


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_update(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    state_file = root / "runtime" / "task_state.json"
    data = load_state(state_file)
    jobs = data.setdefault("jobs", {})
    row = jobs.setdefault(args.job_id, {})

    for key in ("state", "app_id", "target_dir", "last_error"):
        value = getattr(args, key)
        if value is not None:
            row[key] = value
    if args.attempts is not None:
        row["attempts"] = args.attempts
    if args.max_fix is not None:
        row["max_fix"] = args.max_fix

    row["updated_at"] = utc_now()
    save_state(state_file, data)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    state_file = root / "runtime" / "task_state.json"
    data = load_state(state_file)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="task state helper")
    p.add_argument("--root", default=".", help="kg-autonomous root")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("update")
    u.add_argument("--job-id", required=True)
    u.add_argument("--state")
    u.add_argument("--attempts", type=int)
    u.add_argument("--max-fix", type=int)
    u.add_argument("--app-id")
    u.add_argument("--target-dir")
    u.add_argument("--last-error")
    u.set_defaults(func=cmd_update)

    s = sub.add_parser("show")
    s.set_defaults(func=cmd_show)

    ss = sub.add_parser("set-state")
    ss.add_argument("job_id")
    ss.add_argument("state")
    ss.set_defaults(func=cmd_set_state)
    return p


def cmd_set_state(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    state_file = root / "runtime" / "task_state.json"
    data = load_state(state_file)
    jobs = data.setdefault("jobs", {})
    row = jobs.setdefault(args.job_id, {})
    row["state"] = args.state
    row["updated_at"] = utc_now()
    save_state(state_file, data)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
