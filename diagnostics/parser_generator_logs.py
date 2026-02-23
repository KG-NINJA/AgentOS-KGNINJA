#!/usr/bin/env python3
"""Parse generator markers from runtime/activity.log into structured JSON."""

import argparse
import json
import re
from collections import Counter


MARKERS = {
    "primary_rc": re.compile(r"GENERATOR_PRIMARY_RC=([-0-9]+)"),
    "fallback_attempt": re.compile(r"GENERATOR_FALLBACK_ATTEMPT_START"),
    "fallback_invocation": re.compile(r"GENERATOR_FALLBACK_INVOCATION=([^\\s]+)"),
    "fallback_model": re.compile(r"GENERATOR_FALLBACK_MODEL=([^\\s]+)"),
    "fallback_rc": re.compile(r"GENERATOR_FALLBACK_RC=([-0-9]+)"),
    "final_exit_source": re.compile(r"GENERATOR_FINAL_EXIT_SOURCE=([^\\s]+)"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--activity-log", default="runtime/activity.log")
    p.add_argument("--output", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    with open(args.activity_log, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    records = []
    current = {}
    for raw in lines:
        line = raw.strip()
        if "GENERATOR_STAGE=primary_codex_attempt" in line:
            if current:
                records.append(current)
            current = {"stage": "primary_codex_attempt"}
        for key, pat in MARKERS.items():
            m = pat.search(line)
            if not m:
                continue
            if key == "fallback_attempt":
                current["fallback_attempted"] = True
            elif m.groups():
                current[key] = m.group(1)
            else:
                current[key] = True
    if current:
        records.append(current)

    final_sources = Counter(str(r.get("final_exit_source")) for r in records)
    fallback_types = Counter(str(r.get("fallback_invocation")) for r in records if r.get("fallback_invocation"))

    payload = {
        "total_records": len(records),
        "final_exit_source_distribution": dict(final_sources),
        "fallback_type_distribution": dict(fallback_types),
        "records": records,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
