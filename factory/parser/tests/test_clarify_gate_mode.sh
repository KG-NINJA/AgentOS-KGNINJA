#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="runtime"
CONFIG_FILE="config.json"
TMP_DIR="$(mktemp -d)"

cleanup() {
  set +e
  if [ -f "$TMP_DIR/config.json" ]; then
    cp -a "$TMP_DIR/config.json" "$CONFIG_FILE"
  fi
  rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/clarify.json"
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$RUNTIME_DIR"
cp -a "$CONFIG_FILE" "$TMP_DIR/config.json"

write_ir() {
  local confidence="$1"
  cat > "$RUNTIME_DIR/intent_ir.json" <<JSON
{
  "source_queue": "queue/test.md",
  "artifact_targets": ["unknown"],
  "feature_intents": ["notification"],
  "ambiguities": ["artifact target is unknown"],
  "confidence": ${confidence},
  "clarify_required": true
}
JSON
}

# warn_continue should not block.
cat > "$CONFIG_FILE" <<'JSON'
{
  "structured_multi_agent": false,
  "open_md_pipeline_enabled": true,
  "open_md_pipeline_shadow_mode": false,
  "clarify_gate_mode": "warn_continue",
  "clarify_hybrid_min_confidence": 0.3,
  "default_artifact_target": "web_app"
}
JSON
write_ir 0.2
bash ./factory/brain/clarify_gate.sh
jq -e '.blocking == false' "$RUNTIME_DIR/clarify.json" >/dev/null

# block mode must block.
cat > "$CONFIG_FILE" <<'JSON'
{
  "structured_multi_agent": false,
  "open_md_pipeline_enabled": true,
  "open_md_pipeline_shadow_mode": false,
  "clarify_gate_mode": "block",
  "clarify_hybrid_min_confidence": 0.3,
  "default_artifact_target": "web_app"
}
JSON
write_ir 0.9
if bash ./factory/brain/clarify_gate.sh; then
  echo "expected block mode to fail" >&2
  exit 1
fi
jq -e '.blocking == true' "$RUNTIME_DIR/clarify.json" >/dev/null

# hybrid blocks only below threshold.
cat > "$CONFIG_FILE" <<'JSON'
{
  "structured_multi_agent": false,
  "open_md_pipeline_enabled": true,
  "open_md_pipeline_shadow_mode": false,
  "clarify_gate_mode": "hybrid",
  "clarify_hybrid_min_confidence": 0.3,
  "default_artifact_target": "web_app"
}
JSON
write_ir 0.2
if bash ./factory/brain/clarify_gate.sh; then
  echo "expected hybrid low confidence to fail" >&2
  exit 1
fi
write_ir 0.8
bash ./factory/brain/clarify_gate.sh
jq -e '.blocking == false' "$RUNTIME_DIR/clarify.json" >/dev/null

echo "clarify gate mode tests: OK"
