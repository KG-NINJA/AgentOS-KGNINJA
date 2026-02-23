#!/usr/bin/env python3
"""Append experiment run records with refined non-zero classification logic."""

import argparse
import json
import os
import re
from datetime import datetime, timezone


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify run artifact and append one JSONL entry.")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-file", required=True)
    parser.add_argument("--decision-limit-reason-code", required=True)
    return parser.parse_args()


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dependency_error_detected(stderr_text: str) -> bool:
    """Detect explicit external/runtime failures from stderr only."""
    patterns = [
        r"LOCAL_COMPLETION_UNAVAILABLE",
        r"stream disconnected",
        r"connection refused",
        r"temporary failure in name resolution",
        r"network is unreachable",
        r"timed out",
        r"CODEX_EXEC_FAIL",
    ]
    for pat in patterns:
        if re.search(pat, stderr_text, re.IGNORECASE):
            return True
    return False


def classify(artifact: dict, decision_limit_reason_code: str) -> tuple[str, str, bool]:
    exit_code = int(artifact.get("exit_code", 1))
    decision_status = str(artifact.get("decision_status", ""))
    post_gate_status = str(artifact.get("post_gate_status", ""))
    stderr_tail = str(artifact.get("stderr_tail", ""))
    stdout_tail = str(artifact.get("stdout_tail", ""))
    trace_tail = str(artifact.get("trace_tail", ""))
    trace_stage = str(artifact.get("trace_stage", "")).strip().lower()
    trace_reason = str(artifact.get("trace_reason", "")).strip()

    combined = "\n".join([decision_status, post_gate_status, stderr_tail, stdout_tail, trace_tail, trace_reason])
    deterministic_fallback_used = bool(
        re.search(r"GENERATOR_FALLBACK_INVOCATION=deterministic", combined, re.IGNORECASE)
    )

    if exit_code == 0:
        print("RUN_LOGGER_CLASSIFICATION=success_via_exit_zero")
        return "SUCCESS", None, False

    if "status=block" in decision_status and "limit" in combined.lower():
        return "success", decision_limit_reason_code, False

    if trace_stage == "quality_gate":
        return "failure", "QUALITY_GATE_FAIL", True

    if trace_stage == "post_gate":
        reason = trace_reason or post_gate_status or "unknown"
        return "failure", f"POST_GATE_REJECT:{reason}", True

    if trace_stage == "reflex":
        return "failure", "REFLEX_BLOCK", True

    if re.search(r"GENERATION_FORMAT_ERROR", combined, re.IGNORECASE):
        return "failure", "GENERATION_FORMAT_ERROR", True

    if re.search(r"LOCAL_COMPLETION_UNAVAILABLE", combined, re.IGNORECASE):
        return "failure", "LOCAL_COMPLETION_UNAVAILABLE", True

    if deterministic_fallback_used and exit_code != 0:
        return "failure", "LOCAL_FALLBACK_FAILURE", True

    if trace_stage == "generator":
        return "failure", "DEPENDENCY_ERROR", True

    if re.search(r"HARD_POLICY_BLOCK|policy block", combined, re.IGNORECASE):
        return "failure", "HARD_POLICY_BLOCK:EXPLICIT_POLICY_BLOCK", True

    if (not deterministic_fallback_used) and dependency_error_detected(stderr_tail):
        return "failure", "DEPENDENCY_ERROR", True

    return "failure", "UNKNOWN_NONZERO", True


def _normalize_status(status: str) -> str:
    """Canonicalize textual status into SUCCESS/FAIL."""

    normalized = (status or "").strip().upper()
    if normalized == "SUCCESS":
        return "SUCCESS"
    if normalized in {"FAIL", "FAILURE"}:
        return "FAIL"
    raise ValueError(f"unsupported status value: {status!r}")


def main() -> int:
    args = parse_args()
    artifact = read_json(args.artifact_file)

    status, reason_code, hard_block = classify(artifact, args.decision_limit_reason_code)
    status = _normalize_status(status)

    record = {
        "run_id": args.run_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "reason_code": reason_code,
        "hard_block": hard_block,
        "injection_applied": bool(artifact.get("injection_applied", False)),
        "injection_mode": artifact.get("injection_mode"),
        "repair_attempted": bool(artifact.get("repair_attempted", False)),
        "repair_success": bool(artifact.get("repair_success", False)),
        "exit_code": int(artifact.get("exit_code", 1)),
        "trace_stage": artifact.get("trace_stage", ""),
        "trace_reason": artifact.get("trace_reason", ""),
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)
    with open(args.log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
