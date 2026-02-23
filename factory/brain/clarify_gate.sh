#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

INTENT_IR="runtime/intent_ir.json"
OUT_FILE="runtime/clarify.json"
INDEX_LOG="runtime/index.log"
CONFIG_GET="$ROOT/factory/scripts/config_get.py"

if [ ! -f "$INTENT_IR" ]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

clarify_required="$(jq -r '.clarify_required // false' "$INTENT_IR" 2>/dev/null || echo "false")"
if [ "$clarify_required" != "true" ]; then
  exit 0
fi

clarify_gate_mode="warn_continue"
clarify_hybrid_min_confidence="0.30"
if [ -x "$CONFIG_GET" ]; then
  clarify_gate_mode="$("$CONFIG_GET" clarify_gate_mode warn_continue)"
  clarify_hybrid_min_confidence="$("$CONFIG_GET" clarify_hybrid_min_confidence 0.30)"
fi

gate_decision="$(python3 - "$clarify_gate_mode" "$clarify_hybrid_min_confidence" <<'PY'
import json
import sys
from pathlib import Path

ir_path = Path("runtime/intent_ir.json")
out_path = Path("runtime/clarify.json")
data = json.loads(ir_path.read_text(encoding="utf-8"))
mode = str(sys.argv[1]).strip()
threshold_raw = str(sys.argv[2]).strip()
try:
    threshold = float(threshold_raw)
except ValueError:
    threshold = 0.30

if mode not in ("warn_continue", "block", "hybrid"):
    mode = "warn_continue"

targets = data.get("artifact_targets", [])
features = data.get("feature_intents", [])
ambiguities = data.get("ambiguities", [])
confidence = float(data.get("confidence", 0.0) or 0.0)

questions = [
    {
        "id": "artifact_target",
        "question": "Which output should be generated first (web app, desktop app, cli, api, worker)?",
        "reason": "artifact target is ambiguous",
    },
    {
        "id": "runtime_stack",
        "question": "Which runtime stack is mandatory (Python/Node/other)?",
        "reason": "stack constraints are incomplete",
    },
    {
        "id": "must_have_files",
        "question": "List 3-8 required files that must exist in the generated project.",
        "reason": "contract files are not fully specified",
    },
]

blocking = False
if mode == "block":
    blocking = True
elif mode == "hybrid":
    blocking = confidence < threshold

payload = {
    "status": "needs_clarification",
    "source_queue": data.get("source_queue", "none"),
    "blocking": blocking,
    "gate_mode": mode,
    "confidence": confidence,
    "hybrid_min_confidence": threshold,
    "artifact_targets": targets,
    "feature_intents": features,
    "ambiguities": ambiguities,
    "resolution_defaults": {
        "artifact_target": "web_app",
    },
    "questions": questions,
}
out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("block" if blocking else "warn")
PY
)"

confidence="$(jq -r '.confidence // 0' "$INTENT_IR" 2>/dev/null || echo "0")"
printf '%s CLARIFY status=%s mode=%s confidence=%s reason=clarify_required\n' \
  "$(date -Is)" "$gate_decision" "$clarify_gate_mode" "$confidence" >> "$INDEX_LOG"

if [ "$gate_decision" = "block" ]; then
  echo "CLARIFY_GATE: blocked by clarify_required"
  exit 1
fi

echo "CLARIFY_GATE: clarify_required (warn_continue)"
exit 0
