#!/usr/bin/env python3
"""Safety stop controller for self-controlled evolution batches."""

import argparse
import json
import os
import time
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def classify_row(row: dict, decision_limit_reason_code: str) -> str:
    reason = str(row.get("reason_code", ""))
    status = str(row.get("status", ""))

    if reason == decision_limit_reason_code:
        return "DECISION_LIMIT"
    if reason.startswith("HARD_POLICY_BLOCK:REFLEX_BLOCK") or reason.startswith("HARD_POLICY_BLOCK:QUALITY_GATE_BLOCK"):
        return "REFLEX/QUALITY_GATE_BLOCK"
    if status == "failure" and (
        reason.startswith("POST_GATE_REJECT:") or reason.startswith("HARD_POLICY_BLOCK:")
    ):
        return "REAL_FAILURE"
    return "UNKNOWN_OR_SUCCESS"


def failure_rate(rows: list[dict]) -> float:
    total = len(rows)
    if total == 0:
        return 0.0
    fail = sum(1 for r in rows if str(r.get("status", "")) == "failure")
    return fail / total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate whether safe evolution should stop.")
    parser.add_argument("--start-epoch", type=float, default=0.0, help="Epoch when orchestrate_safe started")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = read_json(CONFIG_PATH)
    out_dir = resolve_path(cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)

    baseline_path = os.path.join(out_dir, cfg["logs"]["baseline"])
    kernel_path = os.path.join(out_dir, cfg["logs"]["kernel"])
    state_path = os.path.join(out_dir, cfg["logs"].get("safety_state", "safety_state.json"))

    baseline_rows = load_jsonl(baseline_path)
    kernel_rows = load_jsonl(kernel_path)
    all_rows = baseline_rows + kernel_rows

    decision_limit_reason_code = cfg["failure_definition"]["decision_limit_reason_code"]

    class_counts = Counter(classify_row(r, decision_limit_reason_code) for r in all_rows)
    total_runs = len(all_rows)
    most_freq_class = "NONE"
    most_freq_count = 0
    if class_counts:
        most_freq_class, most_freq_count = class_counts.most_common(1)[0]

    most_freq_ratio = (most_freq_count / total_runs) if total_runs else 0.0

    failure_rate_baseline = failure_rate(baseline_rows)
    failure_rate_kernel = failure_rate(kernel_rows)
    absolute_improvement = failure_rate_baseline - failure_rate_kernel

    elapsed = max(0.0, time.time() - args.start_epoch) if args.start_epoch > 0 else 0.0

    # Track consecutive non-improvement batches in persisted state.
    prev = {}
    if os.path.exists(state_path):
        try:
            prev = read_json(state_path)
        except Exception:
            prev = {}

    prev_streak = int(prev.get("no_improvement_streak", 0))
    if absolute_improvement <= 0.0:
        no_improvement_streak = prev_streak + 1
    else:
        no_improvement_streak = 0

    state = {
        "no_improvement_streak": no_improvement_streak,
        "last_absolute_improvement": absolute_improvement,
        "updated_at_epoch": time.time(),
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    should_stop = False
    reason = "continue"

    if total_runs >= int(cfg.get("max_runs", 100)):
        should_stop = True
        reason = "max_runs_reached"
    elif elapsed > float(cfg.get("max_duration_seconds", 3600)) and args.start_epoch > 0:
        should_stop = True
        reason = "max_duration_exceeded"
    elif most_freq_ratio >= float(cfg.get("stability_threshold", 0.9)):
        should_stop = True
        reason = "dominant_classification_stability_threshold_exceeded"
    elif failure_rate_baseline == 1.0 and failure_rate_kernel == 1.0:
        should_stop = True
        reason = "both_baseline_and_kernel_full_failure"
    elif no_improvement_streak >= int(cfg.get("no_improvement_patience", 3)):
        should_stop = True
        reason = "no_improvement_patience_exhausted"

    output = {
        "should_stop": should_stop,
        "reason": reason,
        "diagnostics": {
            "total_runs": total_runs,
            "most_frequent_classification": most_freq_class,
            "most_frequent_ratio": most_freq_ratio,
            "failure_rate_baseline": failure_rate_baseline,
            "failure_rate_kernel": failure_rate_kernel,
            "absolute_improvement": absolute_improvement,
            "elapsed_seconds": elapsed,
            "no_improvement_streak": no_improvement_streak,
            "classification_counts": dict(class_counts),
        },
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
