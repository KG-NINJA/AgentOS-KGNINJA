#!/usr/bin/env python3
"""Print a human-friendly report from analysis_summary.json."""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def format_distribution(title: str, data: dict[str, int]) -> None:
    print(title)
    if not data:
        print("  (none)")
        return
    for reason, count in sorted(data.items()):
        print(f"  - {reason}: {count}")


def main() -> int:
    cfg = read_json(CONFIG_PATH)
    out_dir = resolve_path(cfg["output_dir"])
    analysis_path = os.path.join(out_dir, cfg["logs"]["analysis"])

    if not os.path.exists(analysis_path):
        print(f"Analysis file not found: {analysis_path}")
        print("Run: python3 evolution_eval/analyze.py")
        return 1

    analysis = read_json(analysis_path)
    baseline = analysis["baseline"]
    kernel = analysis["kernel"]
    comparison = analysis["comparison"]

    print("Evolution Evaluation Report")
    print(f"Baseline failure rate: {baseline['failure_rate']:.2%} ({baseline['failure_count']}/{baseline['total_runs']})")
    print(f"Kernel failure rate:   {kernel['failure_rate']:.2%} ({kernel['failure_count']}/{kernel['total_runs']})")
    print(f"Absolute improvement:  {comparison['absolute_improvement']:.2%}")
    print(f"Relative improvement:  {comparison['relative_improvement']:.2%}")
    print()
    format_distribution("Baseline reason codes:", baseline.get("reason_code_distribution", {}))
    print()
    format_distribution("Kernel reason codes:", kernel.get("reason_code_distribution", {}))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
