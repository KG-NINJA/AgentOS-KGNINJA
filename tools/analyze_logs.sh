#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p runtime/learning

python3 - <<'PY'
import json
import os
import re
from datetime import datetime, timezone

root = os.getcwd()
learning_dir = os.path.join(root, "runtime", "learning")
os.makedirs(learning_dir, exist_ok=True)

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def tail_line(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            return lines[-1] if lines else ""
    except Exception:
        return ""

spec = read_json("runtime/spec.json", {})
interpret = read_json("runtime/interpret.json", {})
entities = read_json("runtime/entities.json", {})
queue_target = read_text("runtime/.queue_target")
last_project = read_text("runtime/.last_generated_project")

index_lines = []
try:
    with open("runtime/index.log", "r", encoding="utf-8") as f:
        index_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
except Exception:
    pass

parser_intent_last = tail_line("runtime/parser_intent.log")
quality_line = ""
for ln in reversed(index_lines):
    if "QUALITY_GATE" in ln:
        quality_line = ln
        break

post_gate_line = ""
for ln in reversed(index_lines):
    if "POST_GATE" in ln:
        post_gate_line = ln
        break

generated_line = ""
for ln in reversed(index_lines):
    if "GENERATED_PROJECT=" in ln:
        generated_line = ln
        break

if generated_line:
    m = re.search(r"GENERATED_PROJECT=(.+)$", generated_line)
    if m:
        last_project = m.group(1).strip()

status = "unknown"
score = None
threshold = None
reason = None
if quality_line:
    if "status=" in quality_line:
        token_map = {}
        for match in re.findall(r'(\w+)=("[^"]*"|\S+)', quality_line):
            key, value = match
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            token_map[key] = value
        status_token = token_map.get("status")
        if status_token == "pass":
            status = "success"
        elif status_token == "fail":
            status = "fail"
        score_token = token_map.get("score")
        threshold_token = token_map.get("threshold")
        if score_token and score_token.isdigit():
            score = int(score_token)
        if threshold_token and threshold_token.isdigit():
            threshold = int(threshold_token)
        reason = token_map.get("reason")
        project_token = token_map.get("project")
        if project_token and project_token != "none":
            last_project = project_token
    else:
        m = re.search(r"QUALITY_GATE score=(\d+) threshold=(\d+) project=(.+)$", quality_line)
        if m:
            score = int(m.group(1))
            threshold = int(m.group(2))
            status = "success" if score >= threshold else "fail"

if status == "unknown":
    if quality_line:
        reason = reason or "quality gate line parse failed"
    else:
        reason = reason or "quality gate line missing"
    status = "fail"

failure_summary = read_json("runtime/failure_summary.json", {})
post_gate = {
    "status": None,
    "reason_code": None,
    "line": post_gate_line or None,
}
if post_gate_line:
    token_map = {}
    for match in re.findall(r'(\w+)=("[^"]*"|\S+)', post_gate_line):
        key, value = match
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        token_map[key] = value
    post_gate["status"] = token_map.get("status")
    post_gate["reason_code"] = token_map.get("reason")

# If post-generation gate failed, always classify the run as fail regardless of quality_gate.
if post_gate.get("status") == "fail":
    status = "fail"
    if not reason:
        reason = f"post_gate:{post_gate.get('reason_code') or 'unknown'}"

record = {
    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "status": status,
    "project": last_project or None,
    "queue_target": queue_target or None,
    "project_type": spec.get("project_type", interpret.get("project_type", "unknown")),
    "ai_task": spec.get("ai_task", interpret.get("ai_task", "unknown")),
    "input_type": spec.get("input_type", interpret.get("input_type", "unknown")),
    "ui_type": spec.get("ui_type", interpret.get("ui_type", "unknown")),
    "quality_policy": spec.get("quality_policy", {}),
    "intent": spec.get("intent", {}),
    "ui_layout": spec.get("ui_layout", {}),
    "api_contract": spec.get("api_contract", {}),
    "runtime_rules": spec.get("runtime_rules", {}),
    "artifacts": spec.get("artifacts", []),
    "contracts": spec.get("contracts", {}),
    "validation_plan": spec.get("validation_plan", {}),
    "clarify_required": spec.get("clarify_required"),
    "ambiguities": spec.get("ambiguities", []),
    "entities": entities,
    "quality_gate": {
        "score": score,
        "threshold": threshold,
        "reason": reason,
        "line": quality_line or None,
    },
    "post_gate": post_gate,
    "failure_summary": failure_summary if isinstance(failure_summary, dict) else {},
    "signals": {
        "parser_intent": parser_intent_last or None,
    },
}

all_log = os.path.join(learning_dir, "all_runs.jsonl")
with open(all_log, "a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")

target_file = "success_cases.jsonl" if record["status"] == "success" else "fail_cases.jsonl"
with open(os.path.join(learning_dir, target_file), "a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")

latest = {
    "status": record["status"],
    "project": record["project"],
    "quality_gate": record["quality_gate"],
    "ai_task": record["ai_task"],
    "project_type": record["project_type"],
}
with open(os.path.join(learning_dir, "latest_summary.json"), "w", encoding="utf-8") as f:
    json.dump(latest, f, ensure_ascii=False, indent=2)
PY

echo "ANALYZE_LOGS: updated runtime/learning records"
