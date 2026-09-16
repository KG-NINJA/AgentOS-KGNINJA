#!/usr/bin/env python3
"""Collect matched Codex CLI evidence without activating a production model.

The runner performs one explicitly selected, read-only side of one frozen case at
a time.  Human/independent grades are imported separately before the existing
Work-platform migration gate is evaluated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
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

SCHEMA = "gpt6-evaluation.v1"
RECEIPT_SCHEMA = "gpt6-evaluation-receipt.v2"
BLOCKED_SCHEMA = "gpt6-execution-blocked.v1"
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


def _claim_attempt(evidence_dir: Path, stem: str) -> Path:
    """Claim one case/side before inference and refuse uncertain re-entry."""
    _ensure_private_directory(evidence_dir)
    completed = (evidence_dir / (stem + ".jsonl"),
                 evidence_dir / (stem + ".receipt.json"))
    if any(path.exists() or path.is_symlink() for path in completed):
        raise kernel.Rejected("completed or partial evidence already exists")
    lock_path = evidence_dir / ("." + stem + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise kernel.Rejected("attempt already in progress or requires reconciliation") from exc
    try:
        os.write(descriptor, b"gpt6-evaluation-attempt\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    # Close the race between the initial evidence check and the exclusive claim.
    if any(path.exists() or path.is_symlink() for path in completed):
        lock_path.unlink()
        raise kernel.Rejected("completed or partial evidence already exists")
    return lock_path


def _blocked_attempt_dir(evidence_dir: Path, stem: str) -> Path:
    root = evidence_dir / "blocked"
    _ensure_private_directory(root)
    attempt = Path(tempfile.mkdtemp(prefix=stem + ".", dir=root))
    os.chmod(attempt, 0o700)
    return attempt


def load_campaign(path: Path) -> dict[str, Any]:
    data = kernel.load_json(path)
    expected = {"schema_version", "baseline_model", "candidate_model", "effort",
                "budget_id", "source_commit", "cases"}
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
    if type(data["source_commit"]) is not str or not COMMIT.fullmatch(data["source_commit"]):
        raise kernel.Rejected("source_commit must be a full Git object id")
    cases = data["cases"]
    if type(cases) is not list or not 30 <= len(cases) <= 1000:
        raise kernel.Rejected("campaign needs 30..1000 cases")
    ids: set[str] = set()
    prompts: set[str] = set()
    categories: set[str] = set()
    for case in cases:
        if type(case) is not dict or set(case) != {"id", "category", "prompt"}:
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
    if not CATEGORIES <= categories:
        raise kernel.Rejected("campaign is missing a required category")
    return data


def case_input_sha(campaign: dict[str, Any], case: dict[str, Any]) -> str:
    return kernel.digest({"prompt": case["prompt"], "source_commit": campaign["source_commit"]})


def _case(campaign: dict[str, Any], case_id: str) -> dict[str, Any]:
    kernel.identifier(case_id)
    matches = [case for case in campaign["cases"] if case["id"] == case_id]
    if len(matches) != 1:
        raise kernel.Rejected("unknown case")
    return matches[0]


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
        event = json.loads(line)
        if type(event) is not dict or type(event.get("type")) is not str:
            raise kernel.Rejected("invalid Codex JSONL event")
        events.append(event)
    completed = [event for event in events if event.get("type") == "turn.completed"]
    failed = [event for event in events if event.get("type") in ("turn.failed", "error")]
    started = [event for event in events if event.get("type") == "thread.started"]
    if len(completed) != 1 or len(started) != 1 or failed:
        raise kernel.Rejected("Codex turn did not complete cleanly")
    usage = completed[0].get("usage")
    if type(usage) is not dict or type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0:
        raise kernel.Rejected("Codex completion is missing usage")
    return events, usage


def _partial_event_summary(raw: bytes) -> dict[str, Any]:
    """Summarize complete JSONL records without exposing event payloads."""
    event_types: list[str] = []
    malformed_lines = 0
    for line in raw[:MAX_EVENT_STREAM_BYTES].splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
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


def execute(model: str, effort: str, prompt: str, workspace: Path, timeout_seconds: int,
            executable: str = "codex") -> tuple[dict[str, Any], bytes]:
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
        events, usage = _parse_events(raw_stdout)
        thread = next(event for event in events if event["type"] == "thread.started")
        thread_id = thread.get("thread_id")
        if type(thread_id) is not str or not thread_id:
            raise kernel.Rejected("Codex completion is missing thread id")
        messages = [event.get("item", {}).get("text") for event in events
                    if event.get("type") == "item.completed"
                    and type(event.get("item")) is dict
                    and event["item"].get("type") == "agent_message"]
        if not messages or type(messages[-1]) is not str:
            raise kernel.Rejected("Codex completion is missing final agent message")
    except (kernel.Rejected, json.JSONDecodeError, UnicodeError) as exc:
        reason = "process-failure" if result.returncode else "invalid-or-incomplete-event-stream"
        summary = _blocked_summary(reason, model, effort, timeout_seconds, latency_ms,
                                   raw_stdout, raw_stderr, result.returncode)
        raise CodexRunBlocked(summary, raw_stdout, raw_stderr) from exc
    if result.returncode:
        summary = _blocked_summary("process-failure", model, effort, timeout_seconds,
                                   latency_ms, raw_stdout, raw_stderr, result.returncode)
        raise CodexRunBlocked(summary, raw_stdout, raw_stderr)
    summary = {"completed": True, "latency_ms": latency_ms,
               "input_tokens": usage["input_tokens"],
               "cached_input_tokens": usage.get("cached_input_tokens"),
               "output_tokens": usage.get("output_tokens"),
               "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
               "thread_id_sha256": _sha_bytes(thread_id.encode()),
               "final_message_sha256": _sha_bytes(messages[-1].encode()),
               "event_stream_sha256": _sha_bytes(raw_stdout)}
    kernel.canonical(summary)
    return summary, raw_stdout


def collect(campaign_path: Path, case_id: str, side: str, workspace: Path,
            evidence_dir: Path, timeout_seconds: int, executable: str = "codex") -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    case = _case(campaign, case_id)
    if side not in ("baseline", "candidate"):
        raise kernel.Rejected("side must be baseline or candidate")
    verify_workspace(workspace, campaign["source_commit"])
    model = campaign[side + "_model"]
    version = _codex_version(executable)
    auth_surface = _codex_auth_surface(executable)
    if auth_surface not in AUTH_SURFACES:
        raise kernel.Rejected("recognized Codex authentication is required")
    stem = case_id + "." + side
    lock_path = _claim_attempt(evidence_dir, stem)
    resolved = False
    try:
        try:
            summary, raw = execute(model, campaign["effort"], case["prompt"], workspace,
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
        # A concurrent local mutation can change what the read-only model saw.
        # Recheck after completion before promoting the output to success evidence.
        verify_workspace(workspace, campaign["source_commit"])
        raw_path = evidence_dir / (stem + ".jsonl")
        receipt_path = evidence_dir / (stem + ".receipt.json")
        _write_private_bytes(raw_path, raw)
        receipt = {"schema_version": RECEIPT_SCHEMA, "campaign_sha256": kernel.digest(campaign),
                   "case_id": case_id, "side": side, "category": case["category"],
                   "requested_model": model, "requested_effort": campaign["effort"],
                   "budget_id": campaign["budget_id"], "input_sha256": case_input_sha(campaign, case),
                   "prompt_sha256": _sha_bytes(case["prompt"].encode()),
                   "source_commit": campaign["source_commit"], "codex_version": version,
                   "auth_surface": auth_surface,
                   "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "provider_model_identity_verified": False, **summary}
        _write_private(receipt_path, receipt)
        resolved = True
        return {**receipt, "receipt_path": str(receipt_path), "raw_path": str(raw_path)}
    finally:
        if resolved:
            lock_path.unlink()


def probe(effort: str, workspace: Path, timeout_seconds: int,
          executable: str = "codex") -> dict[str, Any]:
    version = _codex_version(executable)
    auth_surface = _codex_auth_surface(executable)
    if auth_surface not in AUTH_SURFACES:
        raise kernel.Rejected("recognized Codex authentication is required")
    try:
        summary, _ = execute("gpt-6-astra", effort, PROBE_PROMPT, workspace,
                             timeout_seconds, executable)
    except CodexRunBlocked as exc:
        exc.summary["codex_version"] = version
        exc.summary["auth_surface"] = auth_surface
        raise
    if summary["final_message_sha256"] != _sha_bytes(b"GPT6_ACCESS_PROBE_OK"):
        raise kernel.Rejected("Codex probe response mismatch")
    return {"schema_version": "gpt6-access-probe.v2", "requested_model": "gpt-6-astra",
            "requested_effort": effort, "codex_version": version,
            "auth_surface": auth_surface,
            "requested_model_call_completed": True,
            "provider_model_identity_verified": False,
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **summary}


def compile_report(campaign_path: Path, evidence_dir: Path, grades_path: Path) -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    grades = kernel.load_json(grades_path)
    expected_grades = {"schema_version", "campaign_sha256", "grades"}
    if type(grades) is not dict or set(grades) != expected_grades or grades["schema_version"] != "gpt6-evaluation-grades.v1":
        raise kernel.Rejected("invalid grades schema")
    if grades["campaign_sha256"] != kernel.digest(campaign) or type(grades["grades"]) is not list:
        raise kernel.Rejected("grades do not match campaign")
    grade_map: dict[tuple[str, str], dict[str, Any]] = {}
    required_grade = {"case_id", "side", "safety_pass", "correctness", "evidence_coverage", "cost", "evaluator_ref"}
    for grade in grades["grades"]:
        if type(grade) is not dict or set(grade) != required_grade:
            raise kernel.Rejected("invalid grade")
        key = (kernel.identifier(grade["case_id"]), grade["side"])
        if key in grade_map or key[1] not in ("baseline", "candidate") or type(grade["safety_pass"]) is not bool:
            raise kernel.Rejected("duplicate or invalid grade")
        for metric in ("correctness", "evidence_coverage"):
            value = kernel.number(grade[metric])
            if value > 1:
                raise kernel.Rejected("grade outside 0..1")
        kernel.number(grade["cost"])
        if type(grade["evaluator_ref"]) is not str or not grade["evaluator_ref"]:
            raise kernel.Rejected("missing evaluator reference")
        grade_map[key] = grade
    pairs = []
    campaign_auth_surface: str | None = None
    campaign_codex_version: str | None = None
    for case in campaign["cases"]:
        pair = {"id": case["id"], "input_sha256": case_input_sha(campaign, case),
                "category": case["category"], "budget_id": campaign["budget_id"]}
        for side in ("baseline", "candidate"):
            receipt_path = evidence_dir / f"{case['id']}.{side}.receipt.json"
            raw_path = evidence_dir / f"{case['id']}.{side}.jsonl"
            receipt = kernel.load_json(receipt_path)
            expected = {"schema_version", "campaign_sha256", "case_id", "side", "category",
                        "requested_model", "requested_effort", "budget_id", "input_sha256",
                        "prompt_sha256", "source_commit", "codex_version", "auth_surface",
                        "observed_at",
                        "provider_model_identity_verified", "completed", "latency_ms", "input_tokens",
                        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                        "thread_id_sha256", "final_message_sha256", "event_stream_sha256"}
            if type(receipt) is not dict or set(receipt) != expected:
                raise kernel.Rejected("invalid receipt schema")
            receipt_codex_version = _validated_codex_version(receipt["codex_version"])
            if (receipt["schema_version"] != RECEIPT_SCHEMA or receipt["campaign_sha256"] != kernel.digest(campaign)
                    or receipt["case_id"] != case["id"] or receipt["side"] != side
                    or receipt["category"] != case["category"]
                    or receipt["requested_model"] != campaign[side + "_model"]
                    or receipt["requested_effort"] != campaign["effort"]
                    or receipt["budget_id"] != campaign["budget_id"]
                    or receipt["input_sha256"] != pair["input_sha256"]
                    or receipt["prompt_sha256"] != _sha_bytes(case["prompt"].encode())
                    or receipt["source_commit"] != campaign["source_commit"]
                    or receipt["auth_surface"] not in AUTH_SURFACES
                    or receipt["provider_model_identity_verified"] is not False):
                raise kernel.Rejected("receipt does not match campaign")
            if campaign_auth_surface is None:
                campaign_auth_surface = receipt["auth_surface"]
            elif receipt["auth_surface"] != campaign_auth_surface:
                raise kernel.Rejected("campaign authentication surface changed")
            if campaign_codex_version is None:
                campaign_codex_version = receipt_codex_version
            elif receipt_codex_version != campaign_codex_version:
                raise kernel.Rejected("campaign Codex CLI version changed")
            raw = raw_path.read_bytes()
            _parse_events(raw)
            if _sha_bytes(raw) != receipt["event_stream_sha256"]:
                raise kernel.Rejected("raw event evidence does not match receipt")
            grade = grade_map.get((case["id"], side))
            if grade is None:
                raise kernel.Rejected("missing independent grade")
            pair[side] = {"model": receipt["requested_model"], "effort": receipt["requested_effort"],
                          "completed": receipt["completed"], "safety_pass": grade["safety_pass"],
                          "correctness": grade["correctness"], "evidence_coverage": grade["evidence_coverage"],
                          "latency_ms": receipt["latency_ms"], "cost": grade["cost"],
                          "input_tokens": receipt["input_tokens"],
                          "source_ref": f"{receipt_path}#sha256={kernel.digest(receipt)};{grade['evaluator_ref']}",
                          "prompt_sha256": receipt["prompt_sha256"],
                          "input_sha256": receipt["input_sha256"], "budget_id": receipt["budget_id"]}
        pairs.append(pair)
    if len(grade_map) != len(pairs) * 2:
        raise kernel.Rejected("grades contain cases outside campaign")
    report = {"baseline_model": campaign["baseline_model"],
              "candidate_model": campaign["candidate_model"], "pairs": pairs}
    return {"report": report, "gate": kernel.migration_gate(report),
            "comparison_conditions": {"codex_version": campaign_codex_version,
                                      "auth_surface": campaign_auth_surface},
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
    collect_cmd.add_argument("--timeout-seconds", type=int, default=900)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--campaign", type=Path, required=True)
    compile_cmd.add_argument("--evidence-dir", type=Path, required=True)
    compile_cmd.add_argument("--grades", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-campaign":
            campaign = load_campaign(args.campaign)
            output = {"valid": True, "campaign_sha256": kernel.digest(campaign),
                      "case_count": len(campaign["cases"]),
                      "categories": sorted({case["category"] for case in campaign["cases"]})}
        elif args.command == "probe":
            receipt = probe(args.effort, args.workspace, args.timeout_seconds)
            _write_private(args.output, receipt)
            output = {**receipt, "receipt_path": str(args.output)}
        elif args.command == "collect":
            output = collect(args.campaign, args.case_id, args.side, args.workspace,
                             args.evidence_dir, args.timeout_seconds)
        else:
            output = compile_report(args.campaign, args.evidence_dir, args.grades)
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
                "schema_version": "gpt6-access-probe-blocked.v2",
                "provider_model_identity_verified": False,
                "requested_model_call_completed": False,
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
