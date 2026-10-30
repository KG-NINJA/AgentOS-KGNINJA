#!/usr/bin/env python3
"""Collect matched Codex CLI evidence without activating a production model.

The runner performs one explicitly selected, read-only side of one frozen case at
a time.  Human/independent grades are imported separately before the existing
Work-platform migration gate is evaluated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = ROOT / ".agents/skills/gpt6-work-platform/scripts"
sys.path.insert(0, str(KERNEL_DIR))
import work_kernel as kernel  # noqa: E402
from codex_runtime import (CODEX_VERSION, MIN_GPT6_CODEX_VERSION,
                           IncompatibleCodexCli, require_gpt6_cli)  # noqa: E402

SCHEMA = "gpt6-evaluation.v5"
RECEIPT_SCHEMA = "gpt6-evaluation-receipt.v14"
RUNTIME_LOCK_SCHEMA = "gpt6-campaign-runtime.v1"
BLIND_SCHEMA = "gpt6-evaluation-blind.v1"
BLIND_MAP_SCHEMA = "gpt6-evaluation-blind-map.v2"
GRADE_SCHEMA = "gpt6-evaluation-grades.v4"
BLOCKED_SCHEMA = "gpt6-execution-blocked.v1"
CLAIM_SCHEMA = "gpt6-evaluation-claim.v1"
FINALIZATION_SCHEMA = "gpt6-evaluation-finalization.v2"
UNRESOLVED_SCHEMA = "gpt6-execution-unresolved.v2"
UNRESOLVED_AUDIT_SCHEMA = "gpt6-unresolved-audit.v2"
CATEGORIES = {"research", "coding", "files", "tool_routing", "safety"}
AUTH_SURFACES = {"chatgpt", "api_key", "access_token"}
COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
PROBE_PROMPT = "Reply with exactly: GPT6_ACCESS_PROBE_OK. Do not call tools."
MAX_EVENT_STREAM_BYTES = 16 * 1024 * 1024


class CodexRunBlocked(subprocess.SubprocessError):
    """A model request did not complete; expose only bounded diagnostic metadata."""

    def __init__(self, summary: dict[str, Any], raw_stdout: bytes, raw_stderr: bytes):
        super().__init__(summary["reason"])
        self.summary = summary
        self.raw_stdout = raw_stdout
        self.raw_stderr = raw_stderr
        self.receipt_path: str | None = None
        self.raw_path: str | None = None
        self.stderr_path: str | None = None


class CodexRunUnresolved(CodexRunBlocked):
    """A completed process whose comparison conditions could not be revalidated."""


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise kernel.Rejected("private evidence directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise kernel.Rejected("private evidence path must be a directory")
    os.chmod(path, 0o700)


def _write_private_bytes(path: Path, payload: bytes) -> None:
    _ensure_private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_private(path: Path, value: dict[str, Any]) -> None:
    _write_private_bytes(path, kernel.canonical(value) + b"\n")


def _publish_private_exclusive(path: Path, value: dict[str, Any]) -> bool:
    """Publish one complete private JSON file without replacing a winner."""
    _ensure_private_directory(path.parent)
    payload = kernel.canonical(value) + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return False
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def _claim_attempt(evidence_dir: Path, stem: str, campaign_sha256: str,
                   case_id: str, side: str
                   ) -> tuple[Path, dict[str, Any], tuple[bytes, tuple[int, ...]]]:
    """Claim one case/side before inference and refuse uncertain re-entry."""
    _ensure_private_directory(evidence_dir)
    completed = (evidence_dir / (stem + ".jsonl"),
                 evidence_dir / (stem + ".receipt.json"),
                 evidence_dir / (stem + ".finalized.json"))
    if any(path.exists() or path.is_symlink() for path in completed):
        raise kernel.Rejected("completed or partial evidence already exists")
    lock_path = evidence_dir / ("." + stem + ".lock")
    claim = {
        "schema_version": CLAIM_SCHEMA,
        "campaign_sha256": campaign_sha256,
        "case_id": case_id,
        "side": side,
        "attempt_id": secrets.token_hex(32),
    }
    if not _publish_private_exclusive(lock_path, claim):
        raise kernel.Rejected("attempt already in progress or requires reconciliation")
    claim_snapshot = _private_file_snapshot(
        lock_path, 0o600, kernel.MAX_BYTES, "attempt claim")
    if kernel.parse_json(claim_snapshot[0]) != claim:
        raise kernel.Rejected("attempt claim changed during validation")
    # Close the race between the initial evidence check and the exclusive claim.
    if any(path.exists() or path.is_symlink() for path in completed):
        _release_exact_claim(lock_path, claim_snapshot)
        raise kernel.Rejected("completed or partial evidence already exists")
    return lock_path, claim, claim_snapshot


def _release_exact_claim(
        lock_path: Path, claim_snapshot: tuple[bytes, tuple[int, ...]]) -> None:
    """Remove only the exact private claim snapshot owned by this collector."""
    _require_unchanged_private_snapshot(
        lock_path, 0o600, kernel.MAX_BYTES, claim_snapshot, "attempt claim")
    lock_path.unlink()


def _blocked_attempt_dir(evidence_dir: Path, stem: str) -> Path:
    root = evidence_dir / "blocked"
    _ensure_private_directory(root)
    attempt = Path(tempfile.mkdtemp(prefix=stem + ".", dir=root))
    os.chmod(attempt, 0o700)
    return attempt


def _unresolved_attempt_dir(evidence_dir: Path, stem: str) -> Path:
    root = evidence_dir / "unresolved"
    _ensure_private_directory(root)
    attempt = Path(tempfile.mkdtemp(prefix=stem + ".", dir=root))
    os.chmod(attempt, 0o700)
    return attempt


def _counterbalanced_first_sides(source_commit: str,
                                 cases: list[dict[str, Any]]) -> dict[str, str]:
    """Derive a balanced order from frozen inputs instead of operator preference."""
    ranked = sorted(
        (_sha_bytes((source_commit + "\0" + case["id"] + "\0"
                     + _sha_bytes(case["prompt"].encode("utf-8"))).encode("utf-8")),
         case["id"])
        for case in cases
    )
    baseline_count = len(ranked) // 2
    return {case_id: ("baseline" if index < baseline_count else "candidate")
            for index, (_, case_id) in enumerate(ranked)}


def load_campaign(path: Path) -> dict[str, Any]:
    data = kernel.load_json(path)
    expected = {"schema_version", "baseline_model", "candidate_model", "effort",
                "budget_id", "timeout_seconds", "max_pair_gap_seconds",
                "source_commit", "cases"}
    if type(data) is not dict or set(data) != expected:
        raise kernel.Rejected("invalid campaign schema")
    if data["schema_version"] != SCHEMA or data["candidate_model"] != "gpt-6-astra":
        raise kernel.Rejected("unsupported campaign or candidate model")
    kernel.identifier(data["baseline_model"])
    kernel.identifier(data["budget_id"])
    if data["baseline_model"] == data["candidate_model"]:
        raise kernel.Rejected("baseline and candidate must differ")
    if data["effort"] not in ("low", "medium", "high", "xhigh", "max"):
        raise kernel.Rejected("unsupported effort")
    if type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 3600:
        raise kernel.Rejected("timeout_seconds must be 1..3600")
    if (type(data["max_pair_gap_seconds"]) is not int
            or not 1 <= data["max_pair_gap_seconds"] <= 21_600):
        raise kernel.Rejected("max_pair_gap_seconds must be 1..21600")
    if data["max_pair_gap_seconds"] < data["timeout_seconds"]:
        raise kernel.Rejected(
            "max_pair_gap_seconds must be at least timeout_seconds")
    if type(data["source_commit"]) is not str or not COMMIT.fullmatch(data["source_commit"]):
        raise kernel.Rejected("source_commit must be a full Git object id")
    cases = data["cases"]
    if type(cases) is not list or not 30 <= len(cases) <= 1000:
        raise kernel.Rejected("campaign needs 30..1000 cases")
    ids: set[str] = set()
    prompts: set[str] = set()
    categories: set[str] = set()
    first_side_counts = {"baseline": 0, "candidate": 0}
    for case in cases:
        if type(case) is not dict or set(case) != {"id", "category", "prompt", "first_side"}:
            raise kernel.Rejected("invalid case schema")
        case_id = kernel.identifier(case["id"])
        category = kernel.identifier(case["category"])
        prompt = case["prompt"]
        if case_id in ids or type(prompt) is not str or not prompt.strip() or len(prompt.encode()) > 65_536:
            raise kernel.Rejected("duplicate case or invalid prompt")
        prompt_sha = _sha_bytes(prompt.encode("utf-8"))
        if prompt_sha in prompts:
            raise kernel.Rejected("duplicate prompt")
        ids.add(case_id)
        prompts.add(prompt_sha)
        categories.add(category)
        if case["first_side"] not in first_side_counts:
            raise kernel.Rejected("first_side must be baseline or candidate")
        first_side_counts[case["first_side"]] += 1
    if not CATEGORIES <= categories:
        raise kernel.Rejected("campaign is missing a required category")
    if abs(first_side_counts["baseline"] - first_side_counts["candidate"]) > 1:
        raise kernel.Rejected("campaign execution order must be counterbalanced")
    expected_first_sides = _counterbalanced_first_sides(data["source_commit"], cases)
    if any(case["first_side"] != expected_first_sides[case["id"]] for case in cases):
        raise kernel.Rejected("first_side does not match deterministic campaign schedule")
    return data


def case_input_sha(campaign: dict[str, Any], case: dict[str, Any]) -> str:
    return kernel.digest({"prompt": case["prompt"], "source_commit": campaign["source_commit"]})


def _case(campaign: dict[str, Any], case_id: str) -> dict[str, Any]:
    kernel.identifier(case_id)
    matches = [case for case in campaign["cases"] if case["id"] == case_id]
    if len(matches) != 1:
        raise kernel.Rejected("unknown case")
    return matches[0]


def _pair_order(case: dict[str, Any]) -> str:
    return case["first_side"] + "-first"


def _require_first_side_evidence(campaign: dict[str, Any], case: dict[str, Any],
                                 side: str, evidence_dir: Path
                                 ) -> tuple[str | None, str | None,
                                            str | None, str | None, float | None]:
    """Return the exact first-side evidence and runtime that authorize a mate call."""
    first_side = case["first_side"]
    if side == first_side:
        return None, None, None, None, None
    if evidence_dir.is_symlink():
        raise kernel.Rejected("evidence directory must not be a symlink")
    stem = case["id"] + "." + first_side
    raw_path = evidence_dir / (stem + ".jsonl")
    receipt_path = evidence_dir / (stem + ".receipt.json")
    if (raw_path.is_symlink() or receipt_path.is_symlink()
            or not raw_path.is_file() or not receipt_path.is_file()):
        raise kernel.Rejected("campaign-selected first side must complete before second side")
    try:
        receipt, _ = _validated_completed_evidence(
            campaign, case, first_side, evidence_dir)
    except (kernel.Rejected, OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise kernel.Rejected("campaign-selected first side evidence is invalid") from exc
    return (kernel.digest(receipt), receipt["event_stream_sha256"],
            receipt["codex_version"], receipt["auth_surface"],
            _observed_epoch(receipt["observed_at"]))


def _observed_epoch(value: Any) -> float:
    """Parse the exact UTC timestamp emitted by this collector."""
    if type(value) is not str:
        raise kernel.Rejected("receipt has invalid observation time")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise kernel.Rejected("receipt has invalid observation time") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise kernel.Rejected("receipt has invalid observation time")
    return parsed.timestamp()


def _require_mate_window(predecessor_observed_epoch: float | None,
                         current_epoch: float, max_pair_gap_seconds: int,
                         timeout_seconds: int) -> None:
    """Require enough directed observation time for the mate's fixed timeout."""
    if predecessor_observed_epoch is None:
        return
    if current_epoch < predecessor_observed_epoch:
        raise kernel.Rejected(
            "campaign-selected first-side observation is in the future")
    if current_epoch - predecessor_observed_epoch > max_pair_gap_seconds:
        raise kernel.Rejected("campaign-selected pair observation window expired")
    if current_epoch + timeout_seconds > (predecessor_observed_epoch
                                           + max_pair_gap_seconds):
        raise kernel.Rejected(
            "campaign-selected pair observation window cannot accommodate fixed timeout")


def _require_mate_completion_window(predecessor_observed_epoch: float | None,
                                    completed_epoch: float,
                                    max_pair_gap_seconds: int) -> None:
    """Require the completed mate to remain in the directed observation window."""
    if predecessor_observed_epoch is None:
        return
    if completed_epoch < predecessor_observed_epoch:
        raise kernel.Rejected(
            "campaign-selected first-side observation is in the future")
    if completed_epoch - predecessor_observed_epoch > max_pair_gap_seconds:
        raise kernel.Rejected(
            "completed mate exceeded campaign-selected pair observation window")


def verify_workspace(workspace: Path, source_commit: str) -> None:
    root = workspace.resolve(strict=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                           check=True, capture_output=True, text=True, timeout=10).stdout
    ignored = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored",
                              "--exclude-standard", "-z"], check=True, capture_output=True,
                             timeout=10).stdout
    if head != source_commit or dirty or ignored:
        raise kernel.Rejected("workspace must be clean and match source_commit")


def _codex_version(executable: str) -> str:
    return require_gpt6_cli(executable)


def _validated_codex_version(value: Any) -> str:
    """Validate a recorded version without invoking or trusting the current CLI."""
    if type(value) is not str:
        raise kernel.Rejected("receipt Codex CLI version is invalid")
    match = CODEX_VERSION.fullmatch(value)
    if match is None or tuple(int(part) for part in match.groups()) < MIN_GPT6_CODEX_VERSION:
        raise kernel.Rejected("receipt Codex CLI version is incompatible")
    return value


def _codex_auth_surface(executable: str) -> str:
    """Return only a coarse auth class; never retain CLI account output."""
    try:
        result = subprocess.run([executable, "login", "status"], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=10)
    except subprocess.TimeoutExpired:
        return "unknown"
    if result.returncode:
        return "not_authenticated"
    status = b" ".join((_output_bytes(result.stdout),
                        _output_bytes(result.stderr))).lower()
    if b"api key" in status or b"api-key" in status or b"apikey" in status:
        return "api_key"
    if b"access token" in status:
        return "access_token"
    if b"workload identity" in status:
        return "workload_identity"
    if b"chatgpt" in status:
        return "chatgpt"
    return "unknown"


def _parse_events(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(raw) > MAX_EVENT_STREAM_BYTES:
        raise kernel.Rejected("Codex event stream too large")
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = kernel.parse_json(line)
        if type(event) is not dict or type(event.get("type")) is not str:
            raise kernel.Rejected("invalid Codex JSONL event")
        events.append(event)
    completed = [(index, event) for index, event in enumerate(events)
                 if event.get("type") == "turn.completed"]
    failed = [event for event in events if event.get("type") in ("turn.failed", "error")]
    threads = [index for index, event in enumerate(events)
               if event.get("type") == "thread.started"]
    turns = [index for index, event in enumerate(events)
             if event.get("type") == "turn.started"]
    if (len(completed) != 1 or len(threads) != 1 or len(turns) != 1
            or failed):
        raise kernel.Rejected("Codex turn did not complete cleanly")
    completed_index, completed_event = completed[0]
    if not threads[0] < turns[0] < completed_index:
        raise kernel.Rejected("Codex event lifecycle is invalid")
    if threads[0] != 0 or completed_index != len(events) - 1:
        raise kernel.Rejected("Codex event lifecycle does not bound stream")
    usage = completed_event.get("usage")
    if type(usage) is not dict or type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0:
        raise kernel.Rejected("Codex completion is missing usage")
    return events, usage


def _completion_evidence(raw: bytes) -> tuple[dict[str, Any], str]:
    """Derive all completion fields that can be verified from raw Codex JSONL."""
    events, usage = _parse_events(raw)
    thread = next(event for event in events if event["type"] == "thread.started")
    thread_id = thread.get("thread_id")
    if type(thread_id) is not str or not thread_id.strip():
        raise kernel.Rejected("Codex completion is missing thread id")
    turn_started_index = next(index for index, event in enumerate(events)
                              if event["type"] == "turn.started")
    turn_completed_index = next(index for index, event in enumerate(events)
                                if event["type"] == "turn.completed")
    messages = [(index, event.get("item", {}).get("text"))
                for index, event in enumerate(events)
                if event.get("type") == "item.completed"
                and type(event.get("item")) is dict
                and event["item"].get("type") == "agent_message"]
    if any(not turn_started_index < index < turn_completed_index
           for index, _ in messages):
        raise kernel.Rejected("Codex agent message is outside turn lifecycle")
    if (not messages or type(messages[-1][1]) is not str
            or not messages[-1][1].strip()):
        raise kernel.Rejected("Codex completion is missing final agent message")
    final_message = messages[-1][1]
    tokens: dict[str, int | None] = {}
    for name in ("input_tokens", "cached_input_tokens", "output_tokens",
                 "reasoning_output_tokens"):
        value = usage.get(name)
        if name in ("input_tokens", "output_tokens"):
            if type(value) is not int or value < 0:
                raise kernel.Rejected("Codex completion is missing usage")
        elif value is not None and (type(value) is not int or value < 0):
            raise kernel.Rejected("Codex completion has invalid usage")
        tokens[name] = value
    return {
        **tokens,
        "thread_id_sha256": _sha_bytes(thread_id.encode()),
        "final_message_sha256": _sha_bytes(final_message.encode()),
        "event_stream_sha256": _sha_bytes(raw),
    }, final_message


def _validated_completed_evidence(campaign: dict[str, Any], case: dict[str, Any],
                                  side: str, evidence_dir: Path
                                  ) -> tuple[dict[str, Any], str]:
    """Load one completed run and re-derive its evidence-bound fields."""
    _private_directory_snapshot(evidence_dir, 0o700, "completed evidence")
    claim_path = evidence_dir / f".{case['id']}.{side}.lock"
    if claim_path.exists() or claim_path.is_symlink():
        raise kernel.Rejected("completed evidence retains an attempt claim")
    receipt_path = evidence_dir / f"{case['id']}.{side}.receipt.json"
    raw_path = evidence_dir / f"{case['id']}.{side}.jsonl"
    finalization_path = evidence_dir / f"{case['id']}.{side}.finalized.json"
    receipt_snapshot = _private_file_snapshot(
        receipt_path, 0o600, kernel.MAX_BYTES, "completed evidence")
    raw_snapshot = _private_file_snapshot(
        raw_path, 0o600, MAX_EVENT_STREAM_BYTES, "completed evidence")
    receipt = kernel.parse_json(receipt_snapshot[0])
    expected = {"schema_version", "campaign_sha256", "case_id", "side", "pair_order", "category",
                "requested_model", "requested_effort", "budget_id", "input_sha256",
                "timeout_seconds", "max_pair_gap_seconds", "prompt_sha256", "source_commit",
                "codex_version", "auth_surface", "observed_at", "provider_model_identity_verified",
                "completed", "latency_ms", "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "thread_id_sha256", "final_message_sha256",
                "event_stream_sha256", "predecessor_receipt_sha256",
                "predecessor_event_stream_sha256"}
    if type(receipt) is not dict or set(receipt) != expected:
        raise kernel.Rejected("invalid receipt schema")
    if (receipt["schema_version"] != RECEIPT_SCHEMA
            or receipt["campaign_sha256"] != kernel.digest(campaign)
            or receipt["case_id"] != case["id"] or receipt["side"] != side
            or receipt["pair_order"] != _pair_order(case)
            or receipt["category"] != case["category"]
            or receipt["requested_model"] != campaign[side + "_model"]
            or receipt["requested_effort"] != campaign["effort"]
            or receipt["budget_id"] != campaign["budget_id"]
            or receipt["timeout_seconds"] != campaign["timeout_seconds"]
            or receipt["max_pair_gap_seconds"] != campaign["max_pair_gap_seconds"]
            or receipt["input_sha256"] != case_input_sha(campaign, case)
            or receipt["prompt_sha256"] != _sha_bytes(case["prompt"].encode())
            or receipt["source_commit"] != campaign["source_commit"]
            or receipt["auth_surface"] not in AUTH_SURFACES
            or receipt["provider_model_identity_verified"] is not False
            or receipt["completed"] is not True):
        raise kernel.Rejected("receipt does not match campaign")
    observed_epoch = _observed_epoch(receipt["observed_at"])
    predecessor_receipt_sha256: str | None = None
    predecessor_event_stream_sha256: str | None = None
    if side != case["first_side"]:
        predecessor, _ = _validated_completed_evidence(
            campaign, case, case["first_side"], evidence_dir)
        if observed_epoch < _observed_epoch(predecessor["observed_at"]):
            raise kernel.Rejected("paired observations violate campaign order")
        predecessor_receipt_sha256 = kernel.digest(predecessor)
        predecessor_event_stream_sha256 = predecessor["event_stream_sha256"]
    if (receipt["predecessor_receipt_sha256"] != predecessor_receipt_sha256
            or receipt["predecessor_event_stream_sha256"]
            != predecessor_event_stream_sha256):
        raise kernel.Rejected("receipt predecessor evidence does not match")
    _validated_codex_version(receipt["codex_version"])
    raw = raw_snapshot[0]
    completion, final_message = _completion_evidence(raw)
    if _sha_bytes(raw) != receipt["event_stream_sha256"]:
        raise kernel.Rejected("raw event evidence does not match receipt")
    for field, value in completion.items():
        if receipt[field] != value:
            raise kernel.Rejected("receipt completion metrics do not match raw evidence")
    finalization_snapshot = _private_file_snapshot(
        finalization_path, 0o600, kernel.MAX_BYTES,
        "completed evidence finalization marker")
    finalization = kernel.parse_json(finalization_snapshot[0])
    finalization_expected = {
        "schema_version", "campaign_sha256", "case_id", "side",
        "claim_sha256", "receipt_sha256", "event_stream_sha256",
    }
    if (type(finalization) is not dict
            or set(finalization) != finalization_expected
            or finalization["schema_version"] != FINALIZATION_SCHEMA
            or finalization["campaign_sha256"] != kernel.digest(campaign)
            or finalization["case_id"] != case["id"]
            or finalization["side"] != side
            or type(finalization["claim_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", finalization["claim_sha256"])
            or finalization["receipt_sha256"] != kernel.digest(receipt)
            or finalization["event_stream_sha256"] != _sha_bytes(raw)):
        raise kernel.Rejected("completed evidence finalization marker does not match evidence")
    _require_unchanged_private_snapshot(
        receipt_path, 0o600, kernel.MAX_BYTES, receipt_snapshot,
        "completed evidence")
    _require_unchanged_private_snapshot(
        raw_path, 0o600, MAX_EVENT_STREAM_BYTES, raw_snapshot,
        "completed evidence")
    _require_unchanged_private_snapshot(
        finalization_path, 0o600, kernel.MAX_BYTES, finalization_snapshot,
        "completed evidence finalization marker")
    return receipt, final_message


def _completed_campaign_runtime(campaign: dict[str, Any], evidence_dir: Path
                                ) -> tuple[str | None, str | None]:
    """Recover a campaign runtime from completed evidence created before locking."""
    version: str | None = None
    auth_surface: str | None = None
    for case in campaign["cases"]:
        for side in ("baseline", "candidate"):
            stem = f"{case['id']}.{side}"
            raw_path = evidence_dir / (stem + ".jsonl")
            receipt_path = evidence_dir / (stem + ".receipt.json")
            finalization_path = evidence_dir / (stem + ".finalized.json")
            raw_present = raw_path.exists() or raw_path.is_symlink()
            receipt_present = receipt_path.exists() or receipt_path.is_symlink()
            finalization_present = (finalization_path.exists()
                                    or finalization_path.is_symlink())
            if len({raw_present, receipt_present, finalization_present}) != 1:
                raise kernel.Rejected("partial completed evidence requires reconciliation")
            if not raw_present:
                continue
            receipt, _ = _validated_completed_evidence(
                campaign, case, side, evidence_dir)
            receipt_version = _validated_codex_version(receipt["codex_version"])
            if version is None:
                version = receipt_version
                auth_surface = receipt["auth_surface"]
            elif receipt_version != version:
                raise kernel.Rejected("completed campaign evidence has mixed Codex CLI versions")
            elif receipt["auth_surface"] != auth_surface:
                raise kernel.Rejected("completed campaign evidence has mixed authentication surfaces")
    return version, auth_surface


def _campaign_runtime_lock_snapshot(
        campaign: dict[str, Any], lock_path: Path
        ) -> tuple[dict[str, Any], tuple[bytes, tuple[int, ...]]]:
    """Validate one exact private runtime-lock snapshot."""
    expected = {"schema_version", "campaign_sha256", "codex_version", "auth_surface"}
    snapshot = _private_file_snapshot(
        lock_path, 0o600, kernel.MAX_BYTES, "campaign runtime lock")
    locked = kernel.parse_json(snapshot[0])
    if (type(locked) is not dict or set(locked) != expected
            or locked["schema_version"] != RUNTIME_LOCK_SCHEMA
            or locked["campaign_sha256"] != kernel.digest(campaign)
            or locked["auth_surface"] not in AUTH_SURFACES):
        raise kernel.Rejected("campaign runtime lock is invalid")
    _validated_codex_version(locked["codex_version"])
    return locked, snapshot


def _bind_campaign_runtime(campaign: dict[str, Any], evidence_dir: Path,
                           version: str, auth_surface: str
                           ) -> tuple[bytes, tuple[int, ...]]:
    """Atomically bind all model calls in one campaign to one runtime surface."""
    version = _validated_codex_version(version)
    if auth_surface not in AUTH_SURFACES:
        raise kernel.Rejected("recognized Codex authentication is required")
    _ensure_private_directory(evidence_dir)
    lock_path = evidence_dir / "campaign-runtime.json"
    if lock_path.exists() or lock_path.is_symlink():
        locked, lock_snapshot = _campaign_runtime_lock_snapshot(
            campaign, lock_path)
        if locked["codex_version"] != version:
            raise kernel.Rejected("campaign Codex CLI version differs from runtime lock")
        if locked["auth_surface"] != auth_surface:
            raise kernel.Rejected("campaign authentication surface differs from runtime lock")
        completed_version, completed_auth_surface = _completed_campaign_runtime(
            campaign, evidence_dir)
        if (completed_version is not None
                and completed_version != locked["codex_version"]):
            raise kernel.Rejected("completed evidence differs from campaign runtime lock")
        if (completed_auth_surface is not None
                and completed_auth_surface != locked["auth_surface"]):
            raise kernel.Rejected("completed evidence differs from campaign runtime lock")
        _require_unchanged_private_snapshot(
            lock_path, 0o600, kernel.MAX_BYTES, lock_snapshot,
            "campaign runtime lock")
        return lock_snapshot

    legacy_version, legacy_auth_surface = _completed_campaign_runtime(
        campaign, evidence_dir)
    if legacy_version is not None and version != legacy_version:
        raise kernel.Rejected("campaign Codex CLI version differs from completed evidence")
    if legacy_auth_surface is not None and auth_surface != legacy_auth_surface:
        raise kernel.Rejected("campaign authentication surface differs from completed evidence")

    runtime = {
        "schema_version": RUNTIME_LOCK_SCHEMA,
        "campaign_sha256": kernel.digest(campaign),
        "codex_version": legacy_version or version,
        "auth_surface": legacy_auth_surface or auth_surface,
    }
    _publish_private_exclusive(lock_path, runtime)
    locked, lock_snapshot = _campaign_runtime_lock_snapshot(
        campaign, lock_path)
    if locked["codex_version"] != version:
        raise kernel.Rejected("campaign Codex CLI version differs from runtime lock")
    if locked["auth_surface"] != auth_surface:
        raise kernel.Rejected("campaign authentication surface differs from runtime lock")
    _require_unchanged_private_snapshot(
        lock_path, 0o600, kernel.MAX_BYTES, lock_snapshot,
        "campaign runtime lock")
    return lock_snapshot


def _revalidate_runtime_surface_after_execution(
        executable: str, version: str, auth_surface: str) -> None:
    """Fail closed when the CLI or auth surface changes during one model call."""
    completed_version = _codex_version(executable)
    completed_auth_surface = _codex_auth_surface(executable)
    if completed_version != version:
        raise kernel.Rejected("Codex CLI version changed during model execution")
    if completed_auth_surface != auth_surface:
        raise kernel.Rejected("authentication surface changed during model execution")


def _revalidate_campaign_runtime_after_execution(
        campaign: dict[str, Any], evidence_dir: Path, executable: str,
        version: str, auth_surface: str) -> None:
    """Recheck the runtime surface and campaign lock after one model call."""
    _revalidate_runtime_surface_after_execution(executable, version, auth_surface)
    # Re-read the private lock after the call too. This catches replacement,
    # deletion, permission drift and newly conflicting completed evidence before
    # the current output can be promoted to campaign evidence.
    _bind_campaign_runtime(campaign, evidence_dir, version, auth_surface)


def _partial_event_summary(raw: bytes) -> dict[str, Any]:
    """Summarize complete JSONL records without exposing event payloads."""
    event_types: list[str] = []
    malformed_lines = 0
    for line in raw[:MAX_EVENT_STREAM_BYTES].splitlines():
        if not line.strip():
            continue
        try:
            event = kernel.parse_json(line)
        except (kernel.Rejected, json.JSONDecodeError, UnicodeError):
            malformed_lines += 1
            continue
        if type(event) is not dict or type(event.get("type")) is not str:
            malformed_lines += 1
            continue
        event_types.append(event["type"])
    return {
        "parseable_event_count": len(event_types),
        "malformed_event_line_count": malformed_lines,
        "thread_started_observed": "thread.started" in event_types,
        "turn_started_observed": "turn.started" in event_types,
        "turn_completed_observed": "turn.completed" in event_types,
        "failure_event_observed": any(value in ("turn.failed", "error") for value in event_types),
        "event_scan_truncated": len(raw) > MAX_EVENT_STREAM_BYTES,
    }


def _output_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value if type(value) is bytes else value.encode("utf-8", errors="replace")


def _blocked_summary(reason: str, model: str, effort: str, timeout_seconds: int,
                     latency_ms: float, stdout: bytes, stderr: bytes,
                     returncode: int | None) -> dict[str, Any]:
    summary = {
        "schema_version": BLOCKED_SCHEMA,
        "status": "blocked",
        "reason": reason,
        "requested_model": model,
        "requested_effort": effort,
        "timeout_seconds": timeout_seconds,
        "latency_ms": latency_ms,
        "process_returncode": returncode,
        "stdout_bytes": len(stdout),
        "stdout_sha256": _sha_bytes(stdout),
        "stderr_bytes": len(stderr),
        "stderr_sha256": _sha_bytes(stderr),
        **_partial_event_summary(stdout),
    }
    kernel.canonical(summary)
    return summary


def _preserve_unresolved_attempt(
        campaign: dict[str, Any], case: dict[str, Any], side: str,
        evidence_dir: Path, stem: str, model: str, version: str,
        auth_surface: str, timeout_seconds: int, summary: dict[str, Any],
        raw_stdout: bytes, raw_stderr: bytes, reason: str,
        claim: dict[str, Any], observed_epoch: float) -> CodexRunUnresolved:
    """Retain a completed but unpromotable call without resolving its claim."""
    attempt_dir = _unresolved_attempt_dir(evidence_dir, stem)
    raw_path = attempt_dir / "stdout.jsonl"
    stderr_path = attempt_dir / "stderr"
    receipt_path = attempt_dir / "receipt.json"
    raw_stored = len(raw_stdout) <= MAX_EVENT_STREAM_BYTES
    stderr_stored = len(raw_stderr) <= MAX_EVENT_STREAM_BYTES
    if raw_stored:
        _write_private_bytes(raw_path, raw_stdout)
    if stderr_stored:
        _write_private_bytes(stderr_path, raw_stderr)
    receipt = {
        **_blocked_summary(reason, model, campaign["effort"], timeout_seconds,
                           summary["latency_ms"], raw_stdout, raw_stderr, 0),
        "schema_version": UNRESOLVED_SCHEMA,
        "status": "unresolved",
        "campaign_sha256": kernel.digest(campaign),
        "case_id": case["id"],
        "side": side,
        "pair_order": _pair_order(case),
        "category": case["category"],
        "budget_id": campaign["budget_id"],
        "input_sha256": case_input_sha(campaign, case),
        "prompt_sha256": _sha_bytes(case["prompt"].encode()),
        "source_commit": campaign["source_commit"],
        "codex_version": version,
        "auth_surface": auth_surface,
        "provider_model_identity_verified": False,
        "model_call_process_completed": True,
        "completed": False,
        "comparison_evidence_promoted": False,
        "claim_retained_for_reconciliation": True,
        "claim_sha256": kernel.digest(claim),
        "partial_stdout_stored": raw_stored,
        "private_stderr_stored": stderr_stored,
        "observed_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(observed_epoch)),
    }
    _write_private(receipt_path, receipt)
    exc = CodexRunUnresolved(receipt, raw_stdout, raw_stderr)
    exc.receipt_path = str(receipt_path)
    exc.raw_path = str(raw_path) if raw_stored else None
    exc.stderr_path = str(stderr_path) if stderr_stored else None
    return exc


def _private_file_snapshot(path: Path, mode: int, limit: int,
                           context: str = "unresolved evidence"
                           ) -> tuple[bytes, tuple[int, ...]]:
    """Read one private regular file through a no-follow descriptor."""
    phase = "audit" if context == "unresolved evidence" else "validation"
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise kernel.Rejected(f"{context} path is invalid") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise kernel.Rejected(f"{context} path is invalid")
        if stat.S_IMODE(info.st_mode) != mode:
            raise kernel.Rejected(f"{context} permissions are invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise kernel.Rejected(f"{context} byte limit")
        if len(payload) != info.st_size:
            raise kernel.Rejected(f"{context} changed during {phase}")
        identity = (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        return payload, identity
    finally:
        os.close(descriptor)


def _require_unchanged_private_snapshot(
        path: Path, mode: int, limit: int,
        expected: tuple[bytes, tuple[int, ...]],
        context: str = "unresolved evidence") -> None:
    """Fail closed when evidence changed after it was first validated."""
    try:
        current = _private_file_snapshot(path, mode, limit, context)
    except (kernel.Rejected, OSError) as exc:
        phase = "audit" if context == "unresolved evidence" else "validation"
        raise kernel.Rejected(f"{context} changed during {phase}") from exc
    if current != expected:
        phase = "audit" if context == "unresolved evidence" else "validation"
        raise kernel.Rejected(f"{context} changed during {phase}")


def _private_directory_snapshot(path: Path, mode: int,
                                context: str = "unresolved evidence"
                                ) -> tuple[int, ...]:
    """Open one private directory without following its final component."""
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise kernel.Rejected(f"{context} path is invalid") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise kernel.Rejected(f"{context} path is invalid")
        if stat.S_IMODE(info.st_mode) != mode:
            raise kernel.Rejected(f"{context} permissions are invalid")
        return (info.st_dev, info.st_ino, info.st_mode,
                info.st_mtime_ns, info.st_ctime_ns)
    finally:
        os.close(descriptor)


def audit_unresolved_attempt(campaign_path: Path, case_id: str, side: str,
                             evidence_dir: Path) -> dict[str, Any]:
    """Verify one retained attempt without promoting it or releasing its claim."""
    campaign = load_campaign(campaign_path)
    case = _case(campaign, case_id)
    if side not in ("baseline", "candidate"):
        raise kernel.Rejected("side must be baseline or candidate")
    evidence_dir_snapshot = _private_directory_snapshot(evidence_dir, 0o700)
    stem = case_id + "." + side
    claim_path = evidence_dir / ("." + stem + ".lock")
    claim_snapshot = _private_file_snapshot(claim_path, 0o600, kernel.MAX_BYTES)
    claim = kernel.parse_json(claim_snapshot[0])
    expected_claim = {"schema_version", "campaign_sha256", "case_id", "side",
                      "attempt_id"}
    campaign_sha256 = kernel.digest(campaign)
    if (type(claim) is not dict or set(claim) != expected_claim
            or claim["schema_version"] != CLAIM_SCHEMA
            or claim["campaign_sha256"] != campaign_sha256
            or claim["case_id"] != case_id or claim["side"] != side
            or type(claim["attempt_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", claim["attempt_id"]) is None):
        raise kernel.Rejected("retained attempt claim is invalid")

    unresolved_root = evidence_dir / "unresolved"
    unresolved_root_snapshot = _private_directory_snapshot(unresolved_root, 0o700)
    attempts = [path for path in unresolved_root.iterdir()
                if path.name.startswith(stem + ".")]
    if len(attempts) != 1:
        raise kernel.Rejected("unresolved attempt is missing or ambiguous")
    attempt_dir = attempts[0]
    attempt_dir_snapshot = _private_directory_snapshot(attempt_dir, 0o700)
    receipt_path = attempt_dir / "receipt.json"
    receipt_snapshot = _private_file_snapshot(receipt_path, 0o600, kernel.MAX_BYTES)
    receipt = kernel.parse_json(receipt_snapshot[0])
    expected_receipt = {
        "schema_version", "status", "reason", "requested_model",
        "requested_effort", "timeout_seconds", "latency_ms",
        "process_returncode", "stdout_bytes", "stdout_sha256",
        "stderr_bytes", "stderr_sha256", "parseable_event_count",
        "malformed_event_line_count", "thread_started_observed",
        "turn_started_observed", "turn_completed_observed",
        "failure_event_observed", "event_scan_truncated",
        "campaign_sha256", "case_id", "side", "pair_order", "category",
        "budget_id", "input_sha256", "prompt_sha256", "source_commit",
        "codex_version", "auth_surface", "provider_model_identity_verified",
        "model_call_process_completed", "completed",
        "comparison_evidence_promoted", "claim_retained_for_reconciliation",
        "claim_sha256", "partial_stdout_stored", "private_stderr_stored",
        "observed_at",
    }
    allowed_reasons = {
        "post-execution-pair-window-revalidation-failed",
        "post-execution-runtime-revalidation-failed",
        "post-execution-workspace-revalidation-failed",
    }
    if (type(receipt) is not dict or set(receipt) != expected_receipt
            or receipt["schema_version"] != UNRESOLVED_SCHEMA
            or receipt["status"] != "unresolved"
            or receipt["reason"] not in allowed_reasons
            or receipt["campaign_sha256"] != campaign_sha256
            or receipt["case_id"] != case_id or receipt["side"] != side
            or receipt["pair_order"] != _pair_order(case)
            or receipt["category"] != case["category"]
            or receipt["requested_model"] != campaign[side + "_model"]
            or receipt["requested_effort"] != campaign["effort"]
            or receipt["timeout_seconds"] != campaign["timeout_seconds"]
            or receipt["budget_id"] != campaign["budget_id"]
            or receipt["input_sha256"] != case_input_sha(campaign, case)
            or receipt["prompt_sha256"] != _sha_bytes(case["prompt"].encode())
            or receipt["source_commit"] != campaign["source_commit"]
            or receipt["auth_surface"] not in AUTH_SURFACES
            or receipt["provider_model_identity_verified"] is not False
            or receipt["model_call_process_completed"] is not True
            or receipt["process_returncode"] != 0
            or receipt["completed"] is not False
            or receipt["comparison_evidence_promoted"] is not False
            or receipt["claim_retained_for_reconciliation"] is not True
            or receipt["claim_sha256"] != kernel.digest(claim)):
        raise kernel.Rejected("unresolved receipt does not match retained attempt")
    _validated_codex_version(receipt["codex_version"])
    _observed_epoch(receipt["observed_at"])
    latency_ms = kernel.number(receipt["latency_ms"], positive=True)
    if latency_ms > 3_600_000:
        raise kernel.Rejected("unresolved receipt has invalid latency")
    for field in ("stdout_bytes", "stderr_bytes", "parseable_event_count",
                  "malformed_event_line_count"):
        if type(receipt[field]) is not int or receipt[field] < 0:
            raise kernel.Rejected("unresolved receipt has invalid counts")
    for field in ("stdout_sha256", "stderr_sha256", "claim_sha256"):
        if type(receipt[field]) is not str or kernel.SHA.fullmatch(receipt[field]) is None:
            raise kernel.Rejected("unresolved receipt has invalid hash")
    for field in ("thread_started_observed", "turn_started_observed",
                  "turn_completed_observed", "failure_event_observed",
                  "event_scan_truncated", "partial_stdout_stored",
                  "private_stderr_stored"):
        if type(receipt[field]) is not bool:
            raise kernel.Rejected("unresolved receipt has invalid lifecycle flags")
    if (not receipt["partial_stdout_stored"]
            or not receipt["private_stderr_stored"]
            or receipt["event_scan_truncated"]):
        raise kernel.Rejected("unresolved evidence is incomplete for audit")

    expected_entries = {"receipt.json"}
    private_snapshots = {
        claim_path: (0o600, kernel.MAX_BYTES, claim_snapshot),
        receipt_path: (0o600, kernel.MAX_BYTES, receipt_snapshot),
    }
    raw_path = attempt_dir / "stdout.jsonl"
    if receipt["partial_stdout_stored"]:
        raw_snapshot = _private_file_snapshot(raw_path, 0o600, MAX_EVENT_STREAM_BYTES)
        raw = raw_snapshot[0]
        private_snapshots[raw_path] = (0o600, MAX_EVENT_STREAM_BYTES, raw_snapshot)
        expected_summary = _partial_event_summary(raw)
        if (len(raw) != receipt["stdout_bytes"]
                or _sha_bytes(raw) != receipt["stdout_sha256"]
                or any(receipt[field] != value
                       for field, value in expected_summary.items())):
            raise kernel.Rejected("stored unresolved stdout does not match receipt")
        _completion_evidence(raw)
        expected_entries.add("stdout.jsonl")
    elif receipt["stdout_bytes"] <= MAX_EVENT_STREAM_BYTES or raw_path.exists() or raw_path.is_symlink():
        raise kernel.Rejected("unstored unresolved stdout is inconsistent")

    stderr_path = attempt_dir / "stderr"
    if receipt["private_stderr_stored"]:
        stderr_snapshot = _private_file_snapshot(
            stderr_path, 0o600, MAX_EVENT_STREAM_BYTES)
        stderr = stderr_snapshot[0]
        private_snapshots[stderr_path] = (
            0o600, MAX_EVENT_STREAM_BYTES, stderr_snapshot)
        if (len(stderr) != receipt["stderr_bytes"]
                or _sha_bytes(stderr) != receipt["stderr_sha256"]):
            raise kernel.Rejected("stored unresolved stderr does not match receipt")
        expected_entries.add("stderr")
    elif receipt["stderr_bytes"] <= MAX_EVENT_STREAM_BYTES or stderr_path.exists() or stderr_path.is_symlink():
        raise kernel.Rejected("unstored unresolved stderr is inconsistent")

    if {path.name for path in attempt_dir.iterdir()} != expected_entries:
        raise kernel.Rejected("unresolved attempt contains unexpected evidence")
    completed = (evidence_dir / (stem + ".jsonl"),
                 evidence_dir / (stem + ".receipt.json"))
    if any(path.exists() or path.is_symlink() for path in completed):
        raise kernel.Rejected("unresolved attempt was also promoted")
    for path, (mode, limit, snapshot) in private_snapshots.items():
        _require_unchanged_private_snapshot(path, mode, limit, snapshot)
    try:
        current_directories = (
            _private_directory_snapshot(evidence_dir, 0o700),
            _private_directory_snapshot(unresolved_root, 0o700),
            _private_directory_snapshot(attempt_dir, 0o700),
        )
    except (kernel.Rejected, OSError) as exc:
        raise kernel.Rejected("unresolved evidence changed during audit") from exc
    if current_directories != (
            evidence_dir_snapshot, unresolved_root_snapshot, attempt_dir_snapshot):
        raise kernel.Rejected("unresolved evidence changed during audit")
    # Re-check selection and promotion after the final descriptor reads so the
    # result cannot describe a mixture of two retained attempts.
    if ([path.name for path in unresolved_root.iterdir()
         if path.name.startswith(stem + ".")] != [attempt_dir.name]
            or {path.name for path in attempt_dir.iterdir()} != expected_entries):
        raise kernel.Rejected("unresolved evidence changed during audit")
    if any(path.exists() or path.is_symlink() for path in completed):
        raise kernel.Rejected("unresolved attempt was also promoted")
    return {
        "schema_version": UNRESOLVED_AUDIT_SCHEMA,
        "status": "unresolved_verified",
        "campaign_sha256": campaign_sha256,
        "case_id": case_id,
        "side": side,
        "reason": receipt["reason"],
        "claim_sha256": receipt["claim_sha256"],
        "receipt_sha256": kernel.digest(receipt),
        "stdout_bytes": receipt["stdout_bytes"],
        "stdout_sha256": receipt["stdout_sha256"],
        "stderr_bytes": receipt["stderr_bytes"],
        "stderr_sha256": receipt["stderr_sha256"],
        "model_call_process_completed": True,
        "comparison_evidence_promoted": False,
        "claim_retained_for_reconciliation": True,
        "operator_action_required": True,
    }


def execute(model: str, effort: str, prompt: str, workspace: Path, timeout_seconds: int,
            executable: str = "codex") -> tuple[dict[str, Any], bytes, bytes]:
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        raise kernel.Rejected("unsupported effort")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise kernel.Rejected("timeout must be 1..3600 seconds")
    kernel.identifier(model)
    command = [executable, "--model", model, "-c", "model_reasoning_effort=" + json.dumps(effort),
               "-a", "never", "exec", "--json", "--ephemeral", "--ignore-user-config",
               "--sandbox", "read-only", "--skip-git-repo-check", "-C", str(workspace.resolve()), prompt]
    started = time.monotonic_ns()
    # Codex treats every non-terminal stdin as additional prompt input, including
    # /dev/null. Give it an otherwise unused pseudo-terminal so a prompt supplied
    # as an argument never enters that reader in an automated host.
    try:
        master_fd, slave_fd = os.openpty()
    except OSError as exc:
        raise kernel.Rejected("unable to create terminal stdin for Codex") from exc
    try:
        try:
            result = subprocess.run(command, capture_output=True, stdin=slave_fd,
                                    timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            latency_ms = (time.monotonic_ns() - started) / 1_000_000
            raw_stdout = _output_bytes(exc.stdout)
            raw_stderr = _output_bytes(exc.stderr)
            summary = _blocked_summary("timeout", model, effort, timeout_seconds,
                                       latency_ms, raw_stdout, raw_stderr, None)
            raise CodexRunBlocked(summary, raw_stdout, raw_stderr) from exc
    finally:
        os.close(slave_fd)
        os.close(master_fd)
    latency_ms = (time.monotonic_ns() - started) / 1_000_000
    raw_stdout = _output_bytes(result.stdout)
    raw_stderr = _output_bytes(result.stderr)
    try:
        completion, _ = _completion_evidence(raw_stdout)
    except (kernel.Rejected, json.JSONDecodeError, UnicodeError) as exc:
        reason = "process-failure" if result.returncode else "invalid-or-incomplete-event-stream"
        summary = _blocked_summary(reason, model, effort, timeout_seconds, latency_ms,
                                   raw_stdout, raw_stderr, result.returncode)
        raise CodexRunBlocked(summary, raw_stdout, raw_stderr) from exc
    if result.returncode:
        summary = _blocked_summary("process-failure", model, effort, timeout_seconds,
                                   latency_ms, raw_stdout, raw_stderr, result.returncode)
        raise CodexRunBlocked(summary, raw_stdout, raw_stderr)
    summary = {"completed": True, "latency_ms": latency_ms, **completion}
    kernel.canonical(summary)
    return summary, raw_stdout, raw_stderr


def collect(campaign_path: Path, case_id: str, side: str, workspace: Path,
            evidence_dir: Path, timeout_seconds: int | None = None,
            executable: str = "codex") -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    case = _case(campaign, case_id)
    if side not in ("baseline", "candidate"):
        raise kernel.Rejected("side must be baseline or candidate")
    if timeout_seconds is None:
        timeout_seconds = campaign["timeout_seconds"]
    elif timeout_seconds != campaign["timeout_seconds"]:
        raise kernel.Rejected("timeout does not match campaign")
    (predecessor_receipt_sha256, predecessor_event_stream_sha256,
     predecessor_codex_version, predecessor_auth_surface,
     predecessor_observed_epoch) = \
        _require_first_side_evidence(campaign, case, side, evidence_dir)
    _require_mate_window(predecessor_observed_epoch, time.time(),
                         campaign["max_pair_gap_seconds"], timeout_seconds)
    verify_workspace(workspace, campaign["source_commit"])
    model = campaign[side + "_model"]
    version = _codex_version(executable)
    auth_surface = _codex_auth_surface(executable)
    if auth_surface not in AUTH_SURFACES:
        raise kernel.Rejected("recognized Codex authentication is required")
    if predecessor_codex_version is not None and version != predecessor_codex_version:
        raise kernel.Rejected("mate Codex CLI version differs from campaign-selected first side")
    if predecessor_auth_surface is not None and auth_surface != predecessor_auth_surface:
        raise kernel.Rejected("mate authentication surface differs from campaign-selected first side")
    runtime_lock_snapshot = _bind_campaign_runtime(
        campaign, evidence_dir, version, auth_surface)
    # The predecessor files are operator-owned and live outside the measured
    # checkout. Re-open and re-validate them after runtime preflight so a
    # concurrent replacement cannot authorize the mate with one snapshot and
    # later bind its receipt to another.
    predecessor = (predecessor_receipt_sha256, predecessor_event_stream_sha256,
                   predecessor_codex_version, predecessor_auth_surface,
                   predecessor_observed_epoch)
    try:
        revalidated_predecessor = _require_first_side_evidence(
            campaign, case, side, evidence_dir)
    except kernel.Rejected as exc:
        raise kernel.Rejected(
            "campaign-selected first-side evidence changed during preflight") from exc
    if revalidated_predecessor != predecessor:
        raise kernel.Rejected(
            "campaign-selected first-side evidence changed during preflight")
    predecessor_observed_epoch = revalidated_predecessor[4]
    # Workspace, CLI, authentication and lock checks take time. Re-evaluate at the
    # last pre-claim boundary so the mate still receives the campaign-fixed timeout
    # without necessarily exceeding the directed observation window.
    _require_mate_window(predecessor_observed_epoch, time.time(),
                         campaign["max_pair_gap_seconds"], timeout_seconds)
    # Runtime binding is not the final preflight step. Re-open the exact lock at
    # the last pre-claim boundary too, after predecessor and time-window checks,
    # so a concurrent replacement cannot consume a model call under a different
    # campaign runtime snapshot.
    _require_unchanged_private_snapshot(
        evidence_dir / "campaign-runtime.json", 0o600, kernel.MAX_BYTES,
        runtime_lock_snapshot, "campaign runtime lock")
    stem = case_id + "." + side
    lock_path, claim, claim_snapshot = _claim_attempt(
        evidence_dir, stem, kernel.digest(campaign), case_id, side)
    # Claim publication is itself a filesystem operation after the last pre-claim
    # runtime check. Re-open the originally bound lock once more at the final
    # process-spawn boundary. If it changed, no model process has started, so
    # release only our still-unchanged claim and fail closed.
    try:
        _require_unchanged_private_snapshot(
            evidence_dir / "campaign-runtime.json", 0o600, kernel.MAX_BYTES,
            runtime_lock_snapshot, "campaign runtime lock")
    except kernel.Rejected:
        _release_exact_claim(lock_path, claim_snapshot)
        raise
    resolved = False
    claim_released = False
    try:
        try:
            summary, raw, stderr = execute(
                model, campaign["effort"], case["prompt"], workspace,
                timeout_seconds, executable)
        except CodexRunBlocked as exc:
            attempt_dir = _blocked_attempt_dir(evidence_dir, stem)
            raw_path = attempt_dir / "stdout.jsonl"
            stderr_path = attempt_dir / "stderr"
            receipt_path = attempt_dir / "receipt.json"
            raw_stored = len(exc.raw_stdout) <= MAX_EVENT_STREAM_BYTES
            stderr_stored = len(exc.raw_stderr) <= MAX_EVENT_STREAM_BYTES
            if raw_stored:
                _write_private_bytes(raw_path, exc.raw_stdout)
            if stderr_stored:
                _write_private_bytes(stderr_path, exc.raw_stderr)
            receipt = {
                **exc.summary,
                "campaign_sha256": kernel.digest(campaign),
                "case_id": case_id,
                "side": side,
                "pair_order": _pair_order(case),
                "category": case["category"],
                "budget_id": campaign["budget_id"],
                "input_sha256": case_input_sha(campaign, case),
                "prompt_sha256": _sha_bytes(case["prompt"].encode()),
                "source_commit": campaign["source_commit"],
                "codex_version": version,
                "auth_surface": auth_surface,
                "provider_model_identity_verified": False,
                "completed": False,
                "partial_stdout_stored": raw_stored,
                "private_stderr_stored": stderr_stored,
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _write_private(receipt_path, receipt)
            exc.summary = receipt
            exc.receipt_path = str(receipt_path)
            exc.raw_path = str(raw_path) if raw_stored else None
            exc.stderr_path = str(stderr_path) if stderr_stored else None
            resolved = True
            raise
        completed_observed_epoch = time.time()
        try:
            _require_mate_completion_window(
                predecessor_observed_epoch, completed_observed_epoch,
                campaign["max_pair_gap_seconds"])
        except kernel.Rejected as exc:
            raise _preserve_unresolved_attempt(
                campaign, case, side, evidence_dir, stem, model, version,
                auth_surface, timeout_seconds, summary, raw, stderr,
                "post-execution-pair-window-revalidation-failed", claim,
                completed_observed_epoch) from exc
        try:
            _revalidate_campaign_runtime_after_execution(
                campaign, evidence_dir, executable, version, auth_surface)
        except (kernel.Rejected, IncompatibleCodexCli, OSError,
                subprocess.SubprocessError, json.JSONDecodeError,
                UnicodeError) as exc:
            raise _preserve_unresolved_attempt(
                campaign, case, side, evidence_dir, stem, model, version,
                auth_surface, timeout_seconds, summary, raw, stderr,
                "post-execution-runtime-revalidation-failed", claim,
                completed_observed_epoch) from exc
        # A concurrent local mutation can change what the read-only model saw.
        # Recheck after completion before promoting the output to success evidence.
        try:
            verify_workspace(workspace, campaign["source_commit"])
        except (kernel.Rejected, OSError, subprocess.SubprocessError) as exc:
            raise _preserve_unresolved_attempt(
                campaign, case, side, evidence_dir, stem, model, version,
                auth_surface, timeout_seconds, summary, raw, stderr,
                "post-execution-workspace-revalidation-failed", claim,
                completed_observed_epoch) from exc
        # The claim is the exclusive ownership record for this exact model call.
        # Re-open the original private snapshot after every other post-call check
        # and before publishing comparable success evidence. A replaced claim can
        # no longer prove that this collector still owns the completed attempt.
        try:
            _require_unchanged_private_snapshot(
                lock_path, 0o600, kernel.MAX_BYTES, claim_snapshot,
                "attempt claim")
        except kernel.Rejected as exc:
            raise _preserve_unresolved_attempt(
                campaign, case, side, evidence_dir, stem, model, version,
                auth_surface, timeout_seconds, summary, raw, stderr,
                "post-execution-claim-revalidation-failed", claim,
                completed_observed_epoch) from exc
        raw_path = evidence_dir / (stem + ".jsonl")
        receipt_path = evidence_dir / (stem + ".receipt.json")
        _write_private_bytes(raw_path, raw)
        receipt = {"schema_version": RECEIPT_SCHEMA, "campaign_sha256": kernel.digest(campaign),
                   "case_id": case_id, "side": side, "pair_order": _pair_order(case),
                   "category": case["category"],
                   "requested_model": model, "requested_effort": campaign["effort"],
                    "budget_id": campaign["budget_id"], "timeout_seconds": timeout_seconds,
                    "max_pair_gap_seconds": campaign["max_pair_gap_seconds"],
                   "input_sha256": case_input_sha(campaign, case),
                   "prompt_sha256": _sha_bytes(case["prompt"].encode()),
                   "source_commit": campaign["source_commit"], "codex_version": version,
                   "auth_surface": auth_surface,
                   "observed_at": time.strftime(
                       "%Y-%m-%dT%H:%M:%SZ",
                       time.gmtime(completed_observed_epoch)),
                   "predecessor_receipt_sha256": predecessor_receipt_sha256,
                   "predecessor_event_stream_sha256": predecessor_event_stream_sha256,
                   "provider_model_identity_verified": False, **summary}
        _write_private(receipt_path, receipt)
        # Completion is durable only after the exact claim for this attempt is
        # gone.  Publish a hash-bound marker after that release so a collector
        # failure in this narrow window cannot leave success-looking evidence
        # that downstream grading accepts.
        _release_exact_claim(lock_path, claim_snapshot)
        claim_released = True
        finalization_path = evidence_dir / (stem + ".finalized.json")
        finalization = {
            "schema_version": FINALIZATION_SCHEMA,
            "campaign_sha256": kernel.digest(campaign),
            "case_id": case_id,
            "side": side,
            "claim_sha256": kernel.digest(claim),
            "receipt_sha256": kernel.digest(receipt),
            "event_stream_sha256": _sha_bytes(raw),
        }
        if not _publish_private_exclusive(finalization_path, finalization):
            raise kernel.Rejected("completed evidence finalization marker already exists")
        return {**receipt, "receipt_path": str(receipt_path), "raw_path": str(raw_path)}
    finally:
        if resolved and not claim_released:
            # A receipt is not resolved until this collector releases the exact
            # claim it created. Never delete a replacement claim.
            _release_exact_claim(lock_path, claim_snapshot)


def probe(effort: str, workspace: Path, timeout_seconds: int,
          executable: str = "codex") -> dict[str, Any]:
    version = _codex_version(executable)
    auth_surface = _codex_auth_surface(executable)
    if auth_surface not in AUTH_SURFACES:
        raise kernel.Rejected("recognized Codex authentication is required")
    try:
        summary, raw_stdout, raw_stderr = execute(
            "gpt-6-astra", effort, PROBE_PROMPT, workspace,
            timeout_seconds, executable)
    except CodexRunBlocked as exc:
        exc.summary["codex_version"] = version
        exc.summary["auth_surface"] = auth_surface
        raise
    try:
        _revalidate_runtime_surface_after_execution(executable, version, auth_surface)
    except (kernel.Rejected, IncompatibleCodexCli, OSError,
            subprocess.SubprocessError) as exc:
        blocked = _blocked_summary(
            "runtime-surface-changed-after-completion", "gpt-6-astra", effort,
            timeout_seconds, summary["latency_ms"], raw_stdout, raw_stderr, 0)
        blocked.update({"codex_version": version, "auth_surface": auth_surface,
                        "model_call_process_completed": True})
        raise CodexRunBlocked(blocked, raw_stdout, raw_stderr) from exc
    if summary["final_message_sha256"] != _sha_bytes(b"GPT6_ACCESS_PROBE_OK"):
        blocked = _blocked_summary(
            "probe-response-mismatch", "gpt-6-astra", effort, timeout_seconds,
            summary["latency_ms"], raw_stdout, raw_stderr, 0)
        blocked.update({"codex_version": version, "auth_surface": auth_surface,
                        "model_call_process_completed": True})
        raise CodexRunBlocked(blocked, raw_stdout, raw_stderr)
    return {"schema_version": "gpt6-access-probe.v3", "requested_model": "gpt-6-astra",
            "requested_effort": effort, "codex_version": version,
            "auth_surface": auth_surface,
            "requested_model_call_completed": True,
            "access_probe_validated": True,
            "provider_model_identity_verified": False,
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **summary}


def _blind_sample(campaign: dict[str, Any], case: dict[str, Any], side: str,
                  evidence_dir: Path, sample_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt, final_message = _validated_completed_evidence(
        campaign, case, side, evidence_dir)
    receipt_sha256 = kernel.digest(receipt)
    public_sample = {"sample_id": sample_id, "case_id": case["id"],
                     "category": case["category"], "prompt": case["prompt"],
                     "input_sha256": receipt["input_sha256"],
                     "receipt_sha256": receipt_sha256,
                     "event_stream_sha256": receipt["event_stream_sha256"],
                     "final_message": final_message}
    private_mapping = {"sample_id": sample_id, "case_id": case["id"], "side": side,
                       "receipt_sha256": receipt_sha256,
                       "thread_id_sha256": receipt["thread_id_sha256"],
                       "event_stream_sha256": receipt["event_stream_sha256"]}
    return public_sample, private_mapping


def prepare_blind_grading(campaign_path: Path, evidence_dir: Path,
                          manifest_path: Path, mapping_path: Path) -> dict[str, Any]:
    """Create evaluator material and a separately held identity map.

    The evaluator manifest intentionally excludes model, side, pair order, effort,
    latency, usage, authentication surface and evidence paths. The mapping must not
    be given to the evaluator.
    """
    if manifest_path == mapping_path or any(path.exists() or path.is_symlink()
                                            for path in (manifest_path, mapping_path)):
        raise kernel.Rejected("blind grading outputs must be new distinct files")
    campaign = load_campaign(campaign_path)
    used: set[str] = set()
    event_streams: set[str] = set()
    thread_ids: set[str] = set()
    public_samples: list[dict[str, Any]] = []
    private_mappings: list[dict[str, Any]] = []
    for case in campaign["cases"]:
        for side in ("baseline", "candidate"):
            sample_id = secrets.token_hex(32)
            while sample_id in used:
                sample_id = secrets.token_hex(32)
            used.add(sample_id)
            public, private = _blind_sample(campaign, case, side, evidence_dir, sample_id)
            event_stream_sha256 = private["event_stream_sha256"]
            if event_stream_sha256 in event_streams:
                raise kernel.Rejected("duplicate event stream cannot represent distinct runs")
            event_streams.add(event_stream_sha256)
            thread_id_sha256 = private["thread_id_sha256"]
            if thread_id_sha256 in thread_ids:
                raise kernel.Rejected("duplicate thread id cannot represent distinct runs")
            thread_ids.add(thread_id_sha256)
            public_samples.append(public)
            private_mappings.append(private)
    public_samples.sort(key=lambda sample: sample["sample_id"])
    private_mappings.sort(key=lambda sample: sample["sample_id"])
    manifest = {"schema_version": BLIND_SCHEMA,
                "campaign_sha256": kernel.digest(campaign), "samples": public_samples}
    mapping = {"schema_version": BLIND_MAP_SCHEMA,
               "campaign_sha256": kernel.digest(campaign),
               "blind_manifest_sha256": kernel.digest(manifest), "samples": private_mappings}
    # Store the identity map first. If the second write fails, the private map can
    # be reconciled without exposing a partially generated evaluator manifest.
    _write_private(mapping_path, mapping)
    _write_private(manifest_path, manifest)
    return {"schema_version": BLIND_SCHEMA, "campaign_sha256": kernel.digest(campaign),
            "blind_manifest_sha256": kernel.digest(manifest),
            "sample_count": len(public_samples), "manifest_path": str(manifest_path),
            "mapping_path": str(mapping_path)}


def compile_report(campaign_path: Path, evidence_dir: Path, grades_path: Path,
                   manifest_path: Path, mapping_path: Path) -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    grades = kernel.load_json(grades_path)
    manifest = kernel.load_json(manifest_path)
    mapping = kernel.load_json(mapping_path)
    expected_manifest = {"schema_version", "campaign_sha256", "samples"}
    if (type(manifest) is not dict or set(manifest) != expected_manifest
            or manifest["schema_version"] != BLIND_SCHEMA
            or manifest["campaign_sha256"] != kernel.digest(campaign)
            or type(manifest["samples"]) is not list):
        raise kernel.Rejected("invalid blind manifest")
    manifest_map: dict[str, dict[str, Any]] = {}
    required_public = {"sample_id", "case_id", "category", "prompt", "input_sha256",
                       "receipt_sha256", "event_stream_sha256", "final_message"}
    for sample in manifest["samples"]:
        if (type(sample) is not dict or set(sample) != required_public
                or type(sample["sample_id"]) is not str
                or not kernel.SHA.fullmatch(sample["sample_id"])
                or sample["sample_id"] in manifest_map):
            raise kernel.Rejected("duplicate or invalid blind manifest")
        manifest_map[sample["sample_id"]] = sample
    expected_mapping = {"schema_version", "campaign_sha256", "blind_manifest_sha256", "samples"}
    if (type(mapping) is not dict or set(mapping) != expected_mapping
            or mapping["schema_version"] != BLIND_MAP_SCHEMA
            or mapping["campaign_sha256"] != kernel.digest(campaign)
            or mapping["blind_manifest_sha256"] != kernel.digest(manifest)
            or type(mapping["samples"]) is not list):
        raise kernel.Rejected("invalid blind mapping")
    mapping_map: dict[tuple[str, str], dict[str, Any]] = {}
    sample_ids: set[str] = set()
    required_mapping = {"sample_id", "case_id", "side", "receipt_sha256",
                        "thread_id_sha256", "event_stream_sha256"}
    for sample in mapping["samples"]:
        if type(sample) is not dict or set(sample) != required_mapping:
            raise kernel.Rejected("invalid blind mapping")
        key = (kernel.identifier(sample["case_id"]), sample["side"])
        if (key in mapping_map or key[1] not in ("baseline", "candidate")
                or type(sample["sample_id"]) is not str
                or not kernel.SHA.fullmatch(sample["sample_id"])
                or sample["sample_id"] in sample_ids):
            raise kernel.Rejected("duplicate or invalid blind mapping")
        for field in ("receipt_sha256", "thread_id_sha256", "event_stream_sha256"):
            if type(sample[field]) is not str or not kernel.SHA.fullmatch(sample[field]):
                raise kernel.Rejected("blind mapping is missing an evidence hash")
        mapping_map[key] = sample
        sample_ids.add(sample["sample_id"])
    expected_grades = {"schema_version", "campaign_sha256", "blind_manifest_sha256", "grades"}
    if type(grades) is not dict or set(grades) != expected_grades or grades["schema_version"] != GRADE_SCHEMA:
        raise kernel.Rejected("invalid grades schema")
    if (grades["campaign_sha256"] != kernel.digest(campaign)
            or grades["blind_manifest_sha256"] != mapping["blind_manifest_sha256"]
            or type(grades["grades"]) is not list):
        raise kernel.Rejected("grades do not match campaign")
    grade_map: dict[str, dict[str, Any]] = {}
    required_grade = {"sample_id", "safety_pass", "correctness",
                      "evidence_coverage", "evaluator_ref",
                      "receipt_sha256", "event_stream_sha256"}
    for grade in grades["grades"]:
        if type(grade) is not dict or set(grade) != required_grade:
            raise kernel.Rejected("invalid grade")
        key = grade["sample_id"]
        if (type(key) is not str or not kernel.SHA.fullmatch(key) or key in grade_map
                or key not in sample_ids or type(grade["safety_pass"]) is not bool):
            raise kernel.Rejected("duplicate or invalid grade")
        for metric in ("correctness", "evidence_coverage"):
            value = kernel.number(grade[metric])
            if value > 1:
                raise kernel.Rejected("grade outside 0..1")
        if type(grade["evaluator_ref"]) is not str or not grade["evaluator_ref"]:
            raise kernel.Rejected("missing evaluator reference")
        for field in ("receipt_sha256", "event_stream_sha256"):
            if type(grade[field]) is not str or not kernel.SHA.fullmatch(grade[field]):
                raise kernel.Rejected("grade is missing an evidence hash")
        grade_map[key] = grade
    pairs = []
    campaign_auth_surface: str | None = None
    campaign_codex_version: str | None = None
    event_streams: set[str] = set()
    thread_ids: set[str] = set()
    max_observed_pair_gap_seconds = 0.0
    order_counts = {"baseline-first": 0, "candidate-first": 0}
    for case in campaign["cases"]:
        pair = {"id": case["id"], "input_sha256": case_input_sha(campaign, case),
                "category": case["category"], "budget_id": campaign["budget_id"]}
        observed_at: dict[str, float] = {}
        for side in ("baseline", "candidate"):
            receipt_path = evidence_dir / f"{case['id']}.{side}.receipt.json"
            raw_path = evidence_dir / f"{case['id']}.{side}.jsonl"
            receipt, _ = _validated_completed_evidence(
                campaign, case, side, evidence_dir)
            expected = {"schema_version", "campaign_sha256", "case_id", "side", "pair_order", "category",
                        "requested_model", "requested_effort", "budget_id", "input_sha256",
                        "timeout_seconds", "max_pair_gap_seconds", "prompt_sha256",
                        "source_commit", "codex_version",
                        "auth_surface",
                        "observed_at",
                        "provider_model_identity_verified", "completed", "latency_ms", "input_tokens",
                        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                        "thread_id_sha256", "final_message_sha256", "event_stream_sha256",
                        "predecessor_receipt_sha256", "predecessor_event_stream_sha256"}
            if type(receipt) is not dict or set(receipt) != expected:
                raise kernel.Rejected("invalid receipt schema")
            receipt_codex_version = _validated_codex_version(receipt["codex_version"])
            if (receipt["schema_version"] != RECEIPT_SCHEMA or receipt["campaign_sha256"] != kernel.digest(campaign)
                    or receipt["case_id"] != case["id"] or receipt["side"] != side
                    or receipt["pair_order"] != _pair_order(case)
                    or receipt["category"] != case["category"]
                    or receipt["requested_model"] != campaign[side + "_model"]
                    or receipt["requested_effort"] != campaign["effort"]
                     or receipt["budget_id"] != campaign["budget_id"]
                     or receipt["timeout_seconds"] != campaign["timeout_seconds"]
                     or receipt["max_pair_gap_seconds"] != campaign["max_pair_gap_seconds"]
                    or receipt["input_sha256"] != pair["input_sha256"]
                    or receipt["prompt_sha256"] != _sha_bytes(case["prompt"].encode())
                    or receipt["source_commit"] != campaign["source_commit"]
                    or receipt["auth_surface"] not in AUTH_SURFACES
                    or receipt["provider_model_identity_verified"] is not False):
                raise kernel.Rejected("receipt does not match campaign")
            observed_at[side] = _observed_epoch(receipt["observed_at"])
            if campaign_auth_surface is None:
                campaign_auth_surface = receipt["auth_surface"]
            elif receipt["auth_surface"] != campaign_auth_surface:
                raise kernel.Rejected("campaign authentication surface changed")
            if campaign_codex_version is None:
                campaign_codex_version = receipt_codex_version
            elif receipt_codex_version != campaign_codex_version:
                raise kernel.Rejected("campaign Codex CLI version changed")
            raw = raw_path.read_bytes()
            completion, _ = _completion_evidence(raw)
            event_stream_sha256 = _sha_bytes(raw)
            if event_stream_sha256 != receipt["event_stream_sha256"]:
                raise kernel.Rejected("raw event evidence does not match receipt")
            if event_stream_sha256 in event_streams:
                raise kernel.Rejected("duplicate event stream cannot represent distinct runs")
            event_streams.add(event_stream_sha256)
            thread_id_sha256 = completion["thread_id_sha256"]
            if thread_id_sha256 in thread_ids:
                raise kernel.Rejected("duplicate thread id cannot represent distinct runs")
            thread_ids.add(thread_id_sha256)
            try:
                latency_ms = kernel.number(receipt["latency_ms"], positive=True)
            except kernel.Rejected as exc:
                raise kernel.Rejected("receipt has invalid completion metrics") from exc
            if receipt["completed"] is not True or latency_ms > 3_600_000:
                raise kernel.Rejected("receipt has invalid completion metrics")
            for field, value in completion.items():
                if receipt[field] != value:
                    raise kernel.Rejected("receipt completion metrics do not match raw evidence")
            blind = mapping_map.get((case["id"], side))
            if blind is None:
                raise kernel.Rejected("missing blind mapping")
            public = manifest_map.get(blind["sample_id"])
            expected_public, expected_blind = _blind_sample(campaign, case, side,
                                                            evidence_dir, blind["sample_id"])
            if public != expected_public or blind != expected_blind:
                raise kernel.Rejected("blind manifest or mapping does not match evidence")
            if (blind["receipt_sha256"] != kernel.digest(receipt)
                    or blind["thread_id_sha256"] != thread_id_sha256
                    or blind["event_stream_sha256"] != event_stream_sha256):
                raise kernel.Rejected("blind mapping is not bound to evidence")
            grade = grade_map.get(blind["sample_id"])
            if grade is None:
                raise kernel.Rejected("missing independent grade")
            if (grade["receipt_sha256"] != blind["receipt_sha256"]
                    or grade["event_stream_sha256"] != event_stream_sha256):
                raise kernel.Rejected("independent grade is not bound to evidence")
            pair[side] = {"model": receipt["requested_model"], "effort": receipt["requested_effort"],
                          "completed": receipt["completed"], "safety_pass": grade["safety_pass"],
                          "correctness": grade["correctness"], "evidence_coverage": grade["evidence_coverage"],
                          # There is no authenticated per-run billing evidence for
                          # every supported auth surface. Keep cost neutral so an
                          # evaluator estimate cannot satisfy the migration gate.
                          "latency_ms": receipt["latency_ms"], "cost": 0.0,
                          "input_tokens": receipt["input_tokens"],
                          "output_tokens": receipt["output_tokens"],
                          "total_tokens": receipt["input_tokens"] + receipt["output_tokens"],
                          "source_ref": f"{receipt_path}#sha256={kernel.digest(receipt)};{grade['evaluator_ref']}",
                           "prompt_sha256": receipt["prompt_sha256"],
                           "input_sha256": receipt["input_sha256"], "budget_id": receipt["budget_id"]}
        first_side = case["first_side"]
        mate_side = "candidate" if first_side == "baseline" else "baseline"
        if observed_at[mate_side] < observed_at[first_side]:
            raise kernel.Rejected("paired observations violate campaign order")
        pair_gap = observed_at[mate_side] - observed_at[first_side]
        if pair_gap > campaign["max_pair_gap_seconds"]:
            raise kernel.Rejected("paired observations exceeded max_pair_gap_seconds")
        max_observed_pair_gap_seconds = max(max_observed_pair_gap_seconds, pair_gap)
        order_counts[_pair_order(case)] += 1
        pairs.append(pair)
    if len(grade_map) != len(pairs) * 2:
        raise kernel.Rejected("grades contain cases outside campaign")
    if len(mapping_map) != len(pairs) * 2:
        raise kernel.Rejected("blind mapping contains cases outside campaign")
    if len(manifest_map) != len(pairs) * 2:
        raise kernel.Rejected("blind manifest contains cases outside campaign")
    report = {"baseline_model": campaign["baseline_model"],
              "candidate_model": campaign["candidate_model"], "pairs": pairs}
    return {"report": report, "gate": kernel.migration_gate(report),
            "comparison_conditions": {"codex_version": campaign_codex_version,
                                       "auth_surface": campaign_auth_surface,
                                       "blind_manifest_sha256": mapping["blind_manifest_sha256"],
                                       "independent_grading_blinded": True,
                                       "cost_metric_source": "unavailable_not_evaluator_supplied",
                                       "token_metric_source": "event_input_plus_output_tokens",
                                       "timeout_seconds": campaign["timeout_seconds"],
                                       "max_pair_gap_seconds": campaign["max_pair_gap_seconds"],
                                       "max_observed_pair_gap_seconds": max_observed_pair_gap_seconds,
                                       "baseline_first_pairs": order_counts["baseline-first"],
                                       "candidate_first_pairs": order_counts["candidate-first"]},
            "provider_authenticity_note": "CLI receipts record requested models and coarse authentication surfaces; independent provider identity and entitlement remain unverified."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-campaign")
    validate.add_argument("--campaign", type=Path, required=True)
    access = sub.add_parser("probe")
    access.add_argument("--effort", required=True)
    access.add_argument("--workspace", type=Path, default=ROOT)
    access.add_argument("--timeout-seconds", type=int, default=120)
    access.add_argument("--output", type=Path, default=ROOT / "runtime/gpt6-evaluation/access-probe.json")
    collect_cmd = sub.add_parser("collect")
    collect_cmd.add_argument("--campaign", type=Path, required=True)
    collect_cmd.add_argument("--case-id", required=True)
    collect_cmd.add_argument("--side", choices=("baseline", "candidate"), required=True)
    collect_cmd.add_argument("--workspace", type=Path, required=True)
    collect_cmd.add_argument("--evidence-dir", type=Path, default=ROOT / "runtime/gpt6-evaluation")
    collect_cmd.add_argument("--timeout-seconds", type=int,
                             help="must match the campaign value; omitted uses the campaign")
    audit_cmd = sub.add_parser("audit-unresolved")
    audit_cmd.add_argument("--campaign", type=Path, required=True)
    audit_cmd.add_argument("--case-id", required=True)
    audit_cmd.add_argument("--side", choices=("baseline", "candidate"), required=True)
    audit_cmd.add_argument("--evidence-dir", type=Path, required=True)
    blind_cmd = sub.add_parser("prepare-grading")
    blind_cmd.add_argument("--campaign", type=Path, required=True)
    blind_cmd.add_argument("--evidence-dir", type=Path, required=True)
    blind_cmd.add_argument("--output", type=Path, required=True)
    blind_cmd.add_argument("--mapping-output", type=Path, required=True)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--campaign", type=Path, required=True)
    compile_cmd.add_argument("--evidence-dir", type=Path, required=True)
    compile_cmd.add_argument("--grades", type=Path, required=True)
    compile_cmd.add_argument("--blind-manifest", type=Path, required=True)
    compile_cmd.add_argument("--blind-map", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-campaign":
            campaign = load_campaign(args.campaign)
            output = {"valid": True, "campaign_sha256": kernel.digest(campaign),
                      "case_count": len(campaign["cases"]),
                      "timeout_seconds": campaign["timeout_seconds"],
                      "categories": sorted({case["category"] for case in campaign["cases"]})}
        elif args.command == "probe":
            receipt = probe(args.effort, args.workspace, args.timeout_seconds)
            _write_private(args.output, receipt)
            output = {**receipt, "receipt_path": str(args.output)}
        elif args.command == "collect":
            output = collect(args.campaign, args.case_id, args.side, args.workspace,
                             args.evidence_dir, args.timeout_seconds)
        elif args.command == "audit-unresolved":
            output = audit_unresolved_attempt(
                args.campaign, args.case_id, args.side, args.evidence_dir)
        elif args.command == "prepare-grading":
            output = prepare_blind_grading(args.campaign, args.evidence_dir,
                                           args.output, args.mapping_output)
        else:
            output = compile_report(args.campaign, args.evidence_dir, args.grades,
                                    args.blind_manifest, args.blind_map)
        print(kernel.canonical(output).decode())
        return 0
    except CodexRunBlocked as exc:
        if args.command == "probe":
            raw_path = args.output.with_name(args.output.stem + ".blocked.jsonl")
            stderr_path = args.output.with_name(args.output.stem + ".blocked.stderr")
            raw_stored = len(exc.raw_stdout) <= MAX_EVENT_STREAM_BYTES
            stderr_stored = len(exc.raw_stderr) <= MAX_EVENT_STREAM_BYTES
            receipt = {
                **exc.summary,
                "schema_version": "gpt6-access-probe-blocked.v3",
                "provider_model_identity_verified": False,
                "requested_model_call_completed": bool(
                    exc.summary.get("model_call_process_completed", False)),
                "access_probe_validated": False,
                "partial_stdout_stored": raw_stored,
                "private_stderr_stored": stderr_stored,
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if raw_stored:
                _write_private_bytes(raw_path, exc.raw_stdout)
            if stderr_stored:
                _write_private_bytes(stderr_path, exc.raw_stderr)
            _write_private(args.output, receipt)
            exc.summary = receipt
            exc.receipt_path = str(args.output)
            exc.raw_path = str(raw_path) if raw_stored else None
            exc.stderr_path = str(stderr_path) if stderr_stored else None
        print(kernel.canonical({**exc.summary, "receipt_path": exc.receipt_path,
                                "raw_path": exc.raw_path,
                                "stderr_path": exc.stderr_path}).decode())
        return 2
    except (kernel.Rejected, IncompatibleCodexCli, OSError, subprocess.SubprocessError,
            json.JSONDecodeError, UnicodeError) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
