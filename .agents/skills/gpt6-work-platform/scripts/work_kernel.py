#!/usr/bin/env python3
"""Offline, read-only orchestration kernel. No API client or financial executor.

The host owns the adapter registry, policy, credentials and OS isolation. This
module enforces its own call path, not arbitrary Python or ChatGPT's platform.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

MAX_BYTES = 1_048_576
SECRET_FIELD = re.compile(r"^(private_key|seed_phrase|mnemonic|api_key|access_token|refresh_token|authorization|cookie|password)$", re.I)
SECRET_TEXT = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


class Rejected(ValueError):
    """Invalid, unsafe, incomplete or conflicting input; do not auto-fallback."""


class TransientReadError(Exception):
    """Only a trusted adapter may classify a read failure as retryable."""


def canonical(value: Any) -> bytes:
    """Strict, finite JSON; also refuse common secret fields before persistence."""
    def check(x: Any, depth: int = 0) -> None:
        if depth > 40:
            raise Rejected("JSON nesting limit")
        if x is None or type(x) in (bool, int):
            return
        if type(x) is float and math.isfinite(x):
            return
        if type(x) is str:
            if SECRET_TEXT.search(x):
                raise Rejected("suspected secret")
            return
        if type(x) is list:
            for v in x:
                check(v, depth + 1)
            return
        if type(x) is dict:
            for k, v in x.items():
                if type(k) is not str or SECRET_FIELD.fullmatch(k):
                    raise Rejected("invalid or secret JSON field")
                check(v, depth + 1)
            return
        raise Rejected("value is not finite JSON")
    check(value)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise Rejected("JSON byte limit")
    return data


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(path: Path) -> Any:
    def pairs(items: list) -> dict:
        out: dict = {}
        for k, v in items:
            if k in out:
                raise Rejected("duplicate JSON field")
            out[k] = v
        return out
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Rejected("JSON byte limit")
    value = json.loads(raw, object_pairs_hook=pairs)
    canonical(value)
    return value


def identifier(value: Any) -> str:
    if type(value) is not str or not NAME.fullmatch(value):
        raise Rejected("invalid identifier")
    return value


def number(value: Any, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Rejected("invalid number")
    if value < 0 or (positive and value == 0):
        raise Rejected("number outside allowed range")
    return float(value)


def read_scoped(root: Path, relative: str) -> bytes:
    """Read regular files below an operator-owned root; never follow symlinks.

    The root must not be concurrently mutated by an untrusted local process.
    Secret scanning is defense in depth, not a complete secret detector.
    """
    p = Path(relative)
    if not relative or p.is_absolute() or any(x in ("..", ".") for x in p.parts):
        raise Rejected("unsafe source path")
    if any(x.startswith(".") for x in p.parts) or p.suffix not in (".md", ".txt", ".json"):
        raise Rejected("source type not allowed")
    root = root.resolve(strict=True)
    target = root
    for component in p.parts:
        target = target / component
        if target.is_symlink():
            raise Rejected("symlink source")
    if not target.resolve(strict=True).is_relative_to(root):
        raise Rejected("source escapes root")
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Rejected("source is not regular")
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Rejected("source byte limit")
    text = data.decode("utf-8")
    canonical(text)
    if p.suffix == ".json":
        canonical(json.loads(text))
    return data


def compile_context(root: Path, entries: list[dict], budget_bytes: int = 24000) -> dict:
    """Whole-source selection with required items, pinned hashes, and exclusions.

    budget_bytes is a UTF-8 byte budget, NOT a tokenizer estimate. Caller selects
    relevant sources and must mark every necessary policy/source as required.
    """
    if type(budget_bytes) is not int or not 256 <= budget_bytes <= MAX_BYTES:
        raise Rejected("invalid context budget")
    if type(entries) is not list or len(entries) > 128:
        raise Rejected("invalid context manifest")
    packed: dict = {"sources": [], "excluded": []}
    prepared = []
    for item in entries:
        if type(item) is not dict or set(item) - {"path", "required", "sha256"}:
            raise Rejected("invalid context entry")
        if type(item.get("path")) is not str or type(item.get("required", False)) is not bool:
            raise Rejected("invalid context fields")
        pin = item.get("sha256")
        if pin is not None and (type(pin) is not str or not SHA.fullmatch(pin)):
            raise Rejected("invalid content pin")
        prepared.append(item)
    # Stable manifest order within required/optional classes.
    prepared.sort(key=lambda x: not x.get("required", False))
    seen = set()
    for item in prepared:
        path = item["path"]
        data = read_scoped(root, path)  # Missing/stale/unsafe inputs stop, never vanish.
        sha = hashlib.sha256(data).hexdigest()
        if item.get("sha256", sha) != sha:
            raise Rejected("content pin mismatch")
        if path in seen:
            raise Rejected("duplicate source path")
        seen.add(path)
        source = {"source_ref": path, "sha256": sha,
                  "required": item.get("required", False), "text": data.decode("utf-8")}
        trial = copy.deepcopy(packed)
        trial["sources"].append(source)
        if len(canonical(trial)) <= budget_bytes:
            packed = trial
        elif source["required"]:
            raise Rejected("required context exceeds budget")
        else:
            packed["excluded"].append({"source_ref": path, "sha256": sha, "reason": "byte_budget"})
    if len(canonical(packed)) > budget_bytes:
        raise Rejected("context metadata exceeds budget")
    return packed


@dataclass(frozen=True)
class ReadOperation:
    """Host-created only. A model must never supply adapters or this registry."""
    version: str
    validate: Callable[[dict], None]
    call: Callable[[dict, dict], Awaitable[dict]]
    effect: str = "read"


class EvidenceStore:
    """SQLite atomic claim/finalize. One coordinator writes; workers cannot write.

    Hashes detect accidental corruption, not malicious rewriting by the DB owner.
    A claimed but unfinished run requires operator recovery, never automatic replay.
    """
    def __init__(self, path: Path):
        if path.is_symlink():
            raise Rejected("symlink database")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL, state TEXT NOT NULL, result TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS blobs (hash TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def close(self) -> None:
        self.db.close()

    def claim(self, run_id: str, plan_hash: str) -> dict | None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT plan_hash,state,result FROM runs WHERE id=?", (run_id,)).fetchone()
            if row:
                if row[0] != plan_hash:
                    raise Rejected("run id reused with different plan")
                if row[1] != "complete":
                    raise Rejected("unfinished run requires operator recovery")
                result = json.loads(row[2])
                for event in result["events"]:
                    if "raw_sha256" in event:
                        self.read_blob(event["raw_sha256"])
                self.db.execute("COMMIT")
                return result
            self.db.execute("INSERT INTO runs VALUES (?,?,?,NULL)", (run_id, plan_hash, "running"))
            self.db.execute("COMMIT")
            return None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, run_id: str, result: dict, blobs: Mapping[str, dict]) -> None:
        body = canonical(result).decode("utf-8")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for sha, value in blobs.items():
                raw = canonical(value).decode("utf-8")
                if digest(value) != sha:
                    raise Rejected("blob hash mismatch")
                self.db.execute("INSERT OR IGNORE INTO blobs VALUES (?,?)", (sha, raw))
                if self.read_blob(sha) != value:
                    raise Rejected("existing blob mismatch")
            changed = self.db.execute("UPDATE runs SET state='complete',result=? WHERE id=? AND state='running'", (body, run_id)).rowcount
            if changed != 1:
                raise Rejected("run is not claimable")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def read_blob(self, sha: str) -> dict:
        row = self.db.execute("SELECT body FROM blobs WHERE hash=?", (sha,)).fetchone()
        if row is None:
            raise Rejected("missing raw evidence")
        value = json.loads(row[0])
        if digest(value) != sha:
            raise Rejected("corrupted raw evidence")
        return value


def validate_plan(tasks: list[dict], registry: Mapping[str, ReadOperation], allowlist: tuple[str, ...]) -> list[list[dict]]:
    if type(tasks) is not list or not tasks or len(tasks) > 128:
        raise Rejected("invalid task count")
    ids, todo = set(), {}
    for original in tasks:
        task = copy.deepcopy(original)
        if type(task) is not dict or set(task) != {"id", "operation", "args", "depends_on"}:
            raise Rejected("invalid task schema")
        task_id = identifier(task["id"])
        operation = identifier(task["operation"])
        if task_id in ids:
            raise Rejected("duplicate task id")
        ids.add(task_id)
        if operation not in allowlist or operation not in registry:
            raise Rejected("operation not allowlisted")
        spec = registry[operation]
        if spec.effect != "read":
            raise Rejected("side effects forbidden in read executor")
        identifier(spec.version)
        if type(task["args"]) is not dict or type(task["depends_on"]) is not list:
            raise Rejected("invalid task argument types")
        canonical(task["args"])
        deps = [identifier(d) for d in task["depends_on"]]
        if len(set(deps)) != len(deps):
            raise Rejected("duplicate dependency")
        spec.validate(copy.deepcopy(task["args"]))
        todo[task_id] = task
    if any(d not in ids for t in todo.values() for d in t["depends_on"]):
        raise Rejected("unknown dependency")
    levels, done = [], set()
    while todo:
        ready = [t for t in todo.values() if set(t["depends_on"]) <= done]
        if not ready:
            raise Rejected("cyclic dependencies")
        levels.append(ready)
        for t in ready:
            done.add(t["id"])
            del todo[t["id"]]
    return levels


async def run_reads(run_id: str, tasks: list[dict], registry: Mapping[str, ReadOperation],
                    allowlist: tuple[str, ...], store: EvidenceStore, *,
                    concurrency: int = 3, retries: int = 2, timeout_seconds: float = 15.0, max_age_seconds: float = 300.0) -> dict:
    """Bounded read DAG. Async timeouts require cooperative trusted adapters.

    This is application-level asyncio, NOT provider-native async tool calling.
    There is no arbitrary shell, URL fetch, publishing or payment operation.
    """
    identifier(run_id)
    if type(concurrency) is not int or not 1 <= concurrency <= 3:
        raise Rejected("concurrency must be 1..3")
    if type(retries) is not int or not 0 <= retries <= 2:
        raise Rejected("retries must be 0..2")
    number(timeout_seconds, positive=True)
    number(max_age_seconds, positive=True)
    if timeout_seconds > 300:
        raise Rejected("timeout too large")
    registry = dict(registry)
    levels = validate_plan(tasks, registry, allowlist)
    normalized = [task for level in levels for task in level]
    plan_hash = digest({"tasks": normalized, "adapters": {name: registry[name].version for name in sorted({t['operation'] for t in normalized})},
                        "allowlist": sorted(allowlist), "concurrency": concurrency, "retries": retries, "timeout": timeout_seconds, "max_age": max_age_seconds})
    cached = store.claim(run_id, plan_hash)
    if cached is not None:
        if any(time.time() - e["observed_at"] > max_age_seconds for e in cached["events"] if "observed_at" in e):
            raise Rejected("cached evidence is stale; use a new run id for fresh reads")
        return cached
    semaphore = asyncio.Semaphore(concurrency)
    events: dict = {}
    raw_by_task: dict = {}
    blobs: dict = {}
    permission_stop = False
    async def execute(task: dict) -> tuple[dict, dict | None]:
        nonlocal permission_stop
        event = {"run_id": run_id, "call_id": task["id"], "operation": task["operation"], "attempts": 0}
        if any(events[d]["status"] != "ok" for d in task["depends_on"]):
            return {**event, "status": "skipped", "error_type": "dependency_failed"}, None
        async with semaphore:
            if permission_stop:
                return {**event, "status": "skipped", "error_type": "permission_stop"}, None
            for attempt in range(retries + 1):
                event["attempts"] = attempt + 1
                try:
                    deps = {d: copy.deepcopy(raw_by_task[d]) for d in task["depends_on"]}
                    value = await asyncio.wait_for(registry[task["operation"]].call(copy.deepcopy(task["args"]), deps), timeout_seconds)
                    if type(value) is not dict or set(value) != {"data", "source_refs", "observed_at", "warnings"}:
                        raise Rejected("invalid evidence envelope")
                    if type(value["source_refs"]) is not list or not value["source_refs"] or any(type(s) is not str or not s for s in value["source_refs"]):
                        raise Rejected("missing source references")
                    if type(value["warnings"]) is not list or any(type(w) is not str for w in value["warnings"]):
                        raise Rejected("invalid warnings")
                    observed = number(value["observed_at"], positive=True)
                    now = time.time()
                    if observed > now + 5 or now - observed > max_age_seconds:
                        raise Rejected("stale or future observation timestamp")
                    sha = digest(value)
                    return {**event, "status": "ok", "raw_sha256": sha, "source_refs": value["source_refs"], "observed_at": observed, "warnings": value["warnings"]}, value
                except TransientReadError:
                    if attempt < retries:
                        await asyncio.sleep(0.01 * (2 ** attempt))
                        continue
                    return {**event, "status": "error", "error_type": "transient_exhausted"}, None
                except PermissionError:
                    permission_stop = True
                    return {**event, "status": "denied", "error_type": "permission_denied"}, None
                except (TimeoutError, asyncio.TimeoutError):
                    return {**event, "status": "error", "error_type": "timeout"}, None
                except Exception as exc:
                    # Do not expose arbitrary adapter error messages / credentials.
                    return {**event, "status": "error", "error_type": type(exc).__name__}, None
        raise AssertionError("unreachable")
    for level in levels:
        results = await asyncio.gather(*(execute(t) for t in level))
        for task, (event, raw) in zip(level, results):
            events[task["id"]] = event
            if raw is not None:
                raw_by_task[task["id"]] = raw
                blobs[event["raw_sha256"]] = raw
    result = {"run_id": run_id, "plan_hash": plan_hash, "mode": "read_only",
              "status": "ok" if all(e["status"] == "ok" for e in events.values()) else "incomplete",
              "events": list(events.values()), "recorded_at": time.time(),
              "assurance": "local_hashes_not_third_party_attestation"}
    store.finish(run_id, result, blobs)
    return result


def migration_gate(report: dict) -> dict:
    """Offline paired evidence gate, never proof of provider truth or activation."""
    canonical(report)
    if type(report) is not dict or set(report) != {"baseline_model", "candidate_model", "pairs"}:
        raise Rejected("invalid evaluation schema")
    baseline, candidate = report["baseline_model"], report["candidate_model"]
    identifier(baseline)
    if candidate != "gpt-6-astra" or baseline == candidate:
        raise Rejected("invalid comparison models")
    pairs = report["pairs"]
    if type(pairs) is not list or not 30 <= len(pairs) <= 1000:
        raise Rejected("need 30..1000 completed paired cases")
    ids, inputs, categories, reasons = set(), set(), set(), []
    totals = {side: {m: 0.0 for m in ("latency_ms", "cost", "input_tokens")} for side in ("baseline", "candidate")}
    for pair in pairs:
        if type(pair) is not dict or set(pair) != {"id", "input_sha256", "category", "budget_id", "baseline", "candidate"}:
            raise Rejected("invalid pair schema")
        case_id = identifier(pair["id"])
        sha = pair["input_sha256"]
        if type(sha) is not str or not SHA.fullmatch(sha) or sha in inputs or case_id in ids:
            raise Rejected("duplicate case or invalid input hash")
        ids.add(case_id)
        inputs.add(sha)
        categories.add(identifier(pair["category"]))
        identifier(pair["budget_id"])
        for side, model in (("baseline", baseline), ("candidate", candidate)):
            row = pair[side]
            required = {"model", "effort", "completed", "safety_pass", "correctness", "evidence_coverage", "latency_ms", "cost", "input_tokens", "source_ref", "prompt_sha256", "input_sha256", "budget_id"}
            if type(row) is not dict or set(row) != required:
                raise Rejected("invalid paired result")
            if row["model"] != model or row["input_sha256"] != sha or row["budget_id"] != pair["budget_id"]:
                raise Rejected("model, input or budget mismatch")
            if row["completed"] is not True or row["safety_pass"] is not True:
                reasons.append(case_id + ":incomplete_or_safety_failure")
            if row["effort"] not in ("low", "medium", "high", "xhigh", "max"):
                raise Rejected("unsupported effort")
            if type(row["source_ref"]) is not str or not row["source_ref"]:
                raise Rejected("missing evaluation source")
            if type(row["prompt_sha256"]) is not str or not SHA.fullmatch(row["prompt_sha256"]):
                raise Rejected("missing prompt hash")
            for metric in ("correctness", "evidence_coverage"):
                if number(row[metric]) > 1:
                    raise Rejected("quality score outside 0..1")
            for metric in totals[side]:
                totals[side][metric] += number(row[metric])
        if pair["baseline"]["effort"] != pair["candidate"]["effort"]:
            raise Rejected("preserve effort during initial migration comparison")
        for metric in ("correctness", "evidence_coverage"):
            if pair["candidate"][metric] < pair["baseline"][metric]:
                reasons.append(case_id + ":" + metric + "_regression")
    if not {"research", "coding", "files", "tool_routing", "safety"} <= categories:
        reasons.append("missing_representative_categories")
    improved = [m for m in totals["baseline"] if totals["baseline"][m] > 0 and totals["candidate"][m] <= totals["baseline"][m] * 0.9]
    if not improved:
        reasons.append("no_10_percent_operational_improvement")
    return {"eligible_for_operator_review": not reasons, "activated": False,
            "provider_authenticity_verified": False, "paired_cases": len(pairs),
            "report_sha256": digest(report), "improved_metrics": improved,
            "reasons": sorted(set(reasons)), "totals": totals}


def route_intent(workload: str, profile: dict) -> dict:
    """A plan only: cannot select ChatGPT's model or change a deployment."""
    deterministic = {"collection", "arithmetic", "validation", "deduplication", "accounting"}
    if workload in deterministic:
        return {"route": "deterministic", "model_call": False}
    efforts = profile.get("candidate_effort", {})
    if workload not in efforts or efforts[workload] not in ("low", "medium", "high", "xhigh", "max"):
        raise Rejected("unknown workload or unsupported effort")
    return {"route": "candidate_not_activated", "candidate_model": "gpt-6-astra",
            "candidate_effort": efforts[workload], "preserve_verified_runtime": True,
            "requires": ["model_access_probe", "paired_evaluation", "operator_review", "runtime_integration_test"],
            "financial_authority": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    context = sub.add_parser("context")
    context.add_argument("--root", type=Path, required=True)
    context.add_argument("--manifest", type=Path, required=True)
    context.add_argument("--budget-bytes", type=int, default=24000)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--report", type=Path, required=True)
    route = sub.add_parser("route")
    route.add_argument("--profile", type=Path, required=True)
    route.add_argument("--workload", required=True)
    args = parser.parse_args()
    try:
        if args.command == "context":
            result = compile_context(args.root, load_json(args.manifest), args.budget_bytes)
        elif args.command == "evaluate":
            result = migration_gate(load_json(args.report))
        else:
            result = route_intent(args.workload, load_json(args.profile))
        print(canonical(result).decode("utf-8"))
        if args.command == "evaluate" and not result["eligible_for_operator_review"]:
            raise SystemExit(2)
    except (Rejected, OSError, json.JSONDecodeError, UnicodeError) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
