#!/usr/bin/env python3
"""Parse captured run artifacts and generate structured diagnostics summaries."""

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

CLASSIFICATIONS = [
    "REAL_FAILURE",
    "DECISION_LIMIT",
    "REFLEX/QUALITY_GATE_BLOCK",
    "UNKNOWN_ERROR",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose factory.sh run failures from captured logs.")
    parser.add_argument("--raw-jsonl", required=True, help="Path to raw run JSONL emitted by diagnose_factory.sh")
    parser.add_argument("--output-json", required=True, help="Path to write summary JSON")
    parser.add_argument("--output-text", required=True, help="Path to write human-readable report")
    parser.add_argument("--schema", required=False, help="Optional schema path to reference in output metadata")
    return parser.parse_args()


def read_text(path: str) -> str:
    """Read text safely; return marker text if missing/unreadable."""
    if not path or not os.path.exists(path):
        return "[missing file]"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as exc:
        return f"[read error] {exc}"


def read_jsonl(path: str) -> list[dict]:
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
                # Skip malformed rows so diagnostics can continue.
                continue
    return rows


def classify_run(exit_status: int, corpus: str) -> str:
    """Classify a run using explicit regex rules and fallback handling."""
    post_gate_fail = re.search(r"POST_GATE\s+status=fail|post_gate\s*status\s*=\s*fail|POST_GATE:", corpus, re.IGNORECASE)
    hard_policy_block = re.search(r"HARD_POLICY_BLOCK|hard[_\s-]?policy\s*block", corpus, re.IGNORECASE)
    decision_limit = re.search(r"DECISION:\s*generation paused|DECISION\s+status=block.*limit", corpus, re.IGNORECASE)
    reflex_block = re.search(r"REFLEX\s+status=block|REFLEX:\s*EMERGENCY STOP", corpus, re.IGNORECASE)
    quality_gate_block = re.search(r"QUALITY_GATE\s+status=fail|QUALITY_GATE:\s*.*fail", corpus, re.IGNORECASE)

    if post_gate_fail or hard_policy_block:
        return "REAL_FAILURE"
    if decision_limit:
        return "DECISION_LIMIT"
    if reflex_block or quality_gate_block:
        return "REFLEX/QUALITY_GATE_BLOCK"
    if exit_status != 0:
        return "UNKNOWN_ERROR"
    # Exit 0 with no explicit known marker is treated as unknown for diagnostics visibility.
    return "UNKNOWN_ERROR"


def extract_reason_code(corpus: str, fallback: str = "") -> str:
    """Extract reason code from common log patterns."""
    patterns = [
        r"reason_code=([^\s]+)",
        r"reason=([^\s]+)",
        r"POST_GATE\s+status=fail\s+reason=([^\s]+)",
    ]
    for pat in patterns:
        m = re.search(pat, corpus)
        if m:
            return m.group(1)
    return fallback or ""


def extract_stderr_snippet(stderr_text: str, max_lines: int = 6) -> str:
    lines = [ln for ln in stderr_text.splitlines() if ln.strip()]
    if not lines:
        return "(no stderr)"
    return "\n".join(lines[:max_lines])


def detect_patterns(corpus: str) -> dict[str, bool]:
    """Pattern matching hints for cause/remediation suggestions."""
    return {
        "decision_limit": bool(re.search(r"DECISION:\s*generation paused|status=block.*limit", corpus, re.IGNORECASE)),
        "missing_dependency": bool(re.search(r"not found|No such file|jq not found|command not found", corpus, re.IGNORECASE)),
        "post_gate_fail": bool(re.search(r"POST_GATE\s+status=fail|POST_GATE:", corpus, re.IGNORECASE)),
        "quality_gate_fail": bool(re.search(r"QUALITY_GATE\s+status=fail|QUALITY_GATE:.*fail", corpus, re.IGNORECASE)),
        "reflex_block": bool(re.search(r"REFLEX\s+status=block|EMERGENCY STOP", corpus, re.IGNORECASE)),
        "tests_failed": bool(re.search(r"tests_failed|npm test failed|pytest failed|FAILED", corpus, re.IGNORECASE)),
        "syntax_failed": bool(re.search(r"syntax check failed|SyntaxError|js_syntax_failed|json_syntax_failed", corpus, re.IGNORECASE)),
    }


def suggested_causes(pattern_counts: Counter) -> list[str]:
    causes: list[str] = []
    if pattern_counts["decision_limit"] > 0:
        causes.append("Project generation is being paused by decision limit guardrails.")
    if pattern_counts["missing_dependency"] > 0:
        causes.append("Local dependencies or required commands are missing in the execution environment.")
    if pattern_counts["quality_gate_fail"] > 0:
        causes.append("Quality gate checks are failing after generation.")
    if pattern_counts["reflex_block"] > 0:
        causes.append("Reflex overflow protection is triggering emergency blocks.")
    if pattern_counts["tests_failed"] > 0:
        causes.append("Generated project tests are failing in post-gate validation.")
    if pattern_counts["syntax_failed"] > 0:
        causes.append("Syntax validation failures are occurring in generated artifacts.")
    if not causes:
        causes.append("No dominant known pattern; failures appear as generic unknown errors.")
    return causes


def remediation_steps(pattern_counts: Counter) -> list[str]:
    steps: list[str] = []
    if pattern_counts["decision_limit"] > 0:
        steps.append("Temporarily increase FACTORY_PROJECT_LIMIT for controlled diagnostics runs.")
    if pattern_counts["missing_dependency"] > 0:
        steps.append("Install/verify required commands (for example jq/node/rg) and rerun diagnostics.")
    if pattern_counts["quality_gate_fail"] > 0:
        steps.append("Relax quality_policy thresholds or fix failing quality gate checks.")
    if pattern_counts["reflex_block"] > 0:
        steps.append("Reduce workspace project count or raise reflex/project limit in local test mode.")
    if pattern_counts["tests_failed"] > 0:
        steps.append("Fix generated test failures before counting harness regressions.")
    if pattern_counts["syntax_failed"] > 0:
        steps.append("Repair syntax issues in generated code and enforce syntax checks earlier in pipeline.")
    if not steps:
        steps.append("Capture full stdout/stderr with higher tail length and inspect runtime/index.log transitions.")
    return steps


def build_summary(rows: list[dict], schema_path: str | None = None) -> dict:
    total_runs = len(rows)
    classified_rows: list[dict] = []
    class_counter: Counter = Counter()
    reason_counter: Counter = Counter()
    snippet_by_class: dict[str, str] = {}
    pattern_counter: Counter = Counter()

    for row in rows:
        stdout_text = read_text(row.get("stdout_path", ""))
        stderr_text = read_text(row.get("stderr_path", ""))
        index_text = read_text(row.get("index_tail_path", ""))
        activity_text = read_text(row.get("activity_tail_path", ""))

        corpus = "\n".join([stdout_text, stderr_text, index_text, activity_text])
        exit_status = int(row.get("exit_status", 1))

        cls = classify_run(exit_status, corpus)
        reason_code = extract_reason_code(corpus, fallback=row.get("reason_code", ""))

        class_counter[cls] += 1
        if reason_code:
            reason_counter[reason_code] += 1

        if cls not in snippet_by_class:
            snippet_by_class[cls] = extract_stderr_snippet(stderr_text)

        flags = detect_patterns(corpus)
        for key, hit in flags.items():
            if hit:
                pattern_counter[key] += 1

        classified_rows.append(
            {
                "run_id": row.get("run_id", ""),
                "exit_status": exit_status,
                "classification": cls,
                "reason_code": reason_code,
                "stdout_path": row.get("stdout_path", ""),
                "stderr_path": row.get("stderr_path", ""),
                "index_tail_path": row.get("index_tail_path", ""),
                "activity_tail_path": row.get("activity_tail_path", ""),
            }
        )

    percentages = {
        cls: ((class_counter[cls] / total_runs) * 100.0 if total_runs else 0.0)
        for cls in CLASSIFICATIONS
    }

    most_frequent = max(CLASSIFICATIONS, key=lambda c: class_counter[c]) if CLASSIFICATIONS else ""

    summary = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_path": schema_path or "",
        "total_runs": total_runs,
        "classification_counts": {cls: class_counter[cls] for cls in CLASSIFICATIONS},
        "classification_percentages": percentages,
        "most_frequent_classification": most_frequent,
        "reason_code_distribution": dict(sorted(reason_counter.items())),
        "example_stderr_by_classification": {
            cls: snippet_by_class.get(cls, "(no example)") for cls in CLASSIFICATIONS
        },
        "pattern_counts": dict(sorted(pattern_counter.items())),
        "suggested_causes": suggested_causes(pattern_counter),
        "suggested_remediations": remediation_steps(pattern_counter),
        "runs": classified_rows,
    }
    return summary


def write_text_report(summary: dict, output_text: str) -> None:
    lines: list[str] = []
    lines.append("Factory Diagnostics Report")
    lines.append("=========================")
    lines.append(f"Generated at: {summary['generated_at']}")
    lines.append(f"Total runs : {summary['total_runs']}")
    lines.append("")

    lines.append("Classification counts and percentages:")
    for cls in CLASSIFICATIONS:
        count = summary["classification_counts"].get(cls, 0)
        pct = summary["classification_percentages"].get(cls, 0.0)
        lines.append(f"- {cls}: {count} ({pct:.2f}%)")

    lines.append("")
    lines.append(f"Most frequent classification: {summary['most_frequent_classification']}")
    lines.append("")

    lines.append("Reason code distribution:")
    if summary["reason_code_distribution"]:
        for code, count in summary["reason_code_distribution"].items():
            lines.append(f"- {code}: {count}")
    else:
        lines.append("- (none detected)")

    lines.append("")
    lines.append("Example stderr snippets by classification:")
    for cls in CLASSIFICATIONS:
        lines.append(f"[{cls}]")
        lines.append(summary["example_stderr_by_classification"].get(cls, "(no example)"))
        lines.append("")

    lines.append("Suggested causes:")
    for cause in summary["suggested_causes"]:
        lines.append(f"- {cause}")

    lines.append("")
    lines.append("Suggested remediation steps:")
    for step in summary["suggested_remediations"]:
        lines.append(f"- {step}")

    with open(output_text, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.raw_jsonl)
    summary = build_summary(rows, schema_path=args.schema)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    write_text_report(summary, args.output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
