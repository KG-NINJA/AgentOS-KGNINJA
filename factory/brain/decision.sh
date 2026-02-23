#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

CONFIG_GET="$ROOT/factory/scripts/config_get.py"
STRUCTURED_STATE_FILE="$ROOT/runtime/structured_state.json"
QUEUE_DIR="$ROOT/queue"

structured_flag() {
  if [ -x "$CONFIG_GET" ]; then
    "$CONFIG_GET" structured_multi_agent false
  else
    echo "false"
  fi
}

next_project_dir() {
  mkdir -p "$ROOT/workspace"
  local highest=0
  while IFS= read -r name; do
    case "$name" in
      project-[0-9][0-9][0-9])
        local value="${name#project-}"
        value=$((10#$value))
        if [ "$value" -gt "$highest" ]; then
          highest="$value"
        fi
        ;;
    esac
  done < <(find "$ROOT/workspace" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null || true)
  local next=$((highest + 1))
  printf '%s/project-%03d' "$ROOT/workspace" "$next"
}

relative_to_root() {
  python3 - "$ROOT" "$1" <<'PY'
import os
import sys
root = os.path.abspath(sys.argv[1])
target = os.path.abspath(sys.argv[2])
try:
    rel = os.path.relpath(target, root)
except ValueError:
    rel = target
print(rel)
PY
}

run_structured_pipeline() {
  local queue_file intent_spec architecture_file project_dir critique_file memory_log
  queue_file="$(find "$QUEUE_DIR" -maxdepth 1 -type f -name '*.md' | sort | head -n 1 || true)"
  if [ -z "$queue_file" ]; then
    echo "structured: no queue markdown found" >&2
    return 1
  fi

  intent_spec="$ROOT/runtime/intent_spec.yaml"
  architecture_file="$ROOT/runtime/architecture.md"
  project_dir="$(next_project_dir)"
  critique_file="$ROOT/runtime/critique_report.md"
  memory_log="$ROOT/memory/log-$(date +%Y%m%d).md"

  rm -f "$STRUCTURED_STATE_FILE"

  "$ROOT/factory/roles/planner.sh" "$queue_file" "$intent_spec"
  "$ROOT/factory/roles/architect.sh" "$intent_spec" "$architecture_file"
  "$ROOT/factory/roles/builder.sh" "$architecture_file" "$project_dir"
  "$ROOT/factory/roles/critic.sh" "$project_dir" "$critique_file"
  "$ROOT/factory/roles/reflector.sh" "$critique_file" "$memory_log"

  cat > "$STRUCTURED_STATE_FILE" <<EOF
{
  "project_dir": "$(relative_to_root "$project_dir")",
  "intent_spec": "$(relative_to_root "$intent_spec")",
  "architecture": "$(relative_to_root "$architecture_file")",
  "critique_report": "$(relative_to_root "$critique_file")",
  "memory_log": "$(relative_to_root "$memory_log")"
}
EOF
  printf '%s STRUCTURED_PIPELINE status=success project_dir=%s\n' "$(date -Is)" "$(relative_to_root "$project_dir")" >> "$INDEX_LOG"
  return 0
}

INDEX_LOG="runtime/index.log"
ACTIVITY_LOG="runtime/activity.log"
mkdir -p runtime

total="$(grep TOTAL_PROJECTS "$INDEX_LOG" 2>/dev/null | cut -d= -f2 || echo 0)"
total="${total:-0}"
limit="${FACTORY_PROJECT_LIMIT:-12}"
timestamp() {
  date -Is
}

# Inspect generator outcome before enforcing decision limits.
# This allows fallback-success runs to continue as success and only
# hard-failure signatures to produce an immediate failure result.
latest_exit_source="${GENERATOR_FINAL_EXIT_SOURCE:-}"

case "$latest_exit_source" in
  primary_success|PRIMARY_SUCCESS)
    echo "DECISION: SUCCESS via generator"
    printf '%s DECISION status=pass reason=generator_primary_success total=%s limit=%s\n' "$(timestamp)" "$total" "$limit" >> "$INDEX_LOG"
    exit 0
    ;;
  local_fallback_success|LOCAL_FALLBACK_SUCCESS|deterministic_fallback_success|DETERMINISTIC_FALLBACK_SUCCESS)
    echo "DECISION: SUCCESS via fallback"
    printf '%s DECISION status=pass reason=generator_fallback_success total=%s limit=%s\n' "$(timestamp)" "$total" "$limit" >> "$INDEX_LOG"
    exit 0
    ;;
  local_fallback_failure|LOCAL_FALLBACK_FAILURE|generation_format_error|GENERATION_FORMAT_ERROR|local_completion_unavailable|LOCAL_COMPLETION_UNAVAILABLE|quality_gate_fail|QUALITY_GATE_FAIL|post_gate_reject|POST_GATE_REJECT)
    echo "DECISION: FAIL via $latest_exit_source"
    printf '%s DECISION status=fail reason=%s total=%s limit=%s\n' "$(timestamp)" "$latest_exit_source" "$total" "$limit" >> "$INDEX_LOG"
    exit 1
    ;;
esac

# Unknown/absent generator state should not hard-fail.
if [ -n "$latest_exit_source" ]; then
  echo "DECISION: WARNING unknown generator state: $latest_exit_source (continuing)"
  printf '%s DECISION status=warn reason=unknown_generator_state:%s total=%s limit=%s\n' "$(timestamp)" "$latest_exit_source" "$total" "$limit" >> "$INDEX_LOG"
fi

if [ "$total" -ge "$limit" ]; then
  echo "DECISION: generation paused (limit reached: total=$total limit=$limit)"
  printf '%s DECISION status=block total=%s limit=%s\n' "$(timestamp)" "$total" "$limit" >> "$INDEX_LOG"
  exit 1
fi

printf '%s DECISION status=pass total=%s limit=%s\n' "$(timestamp)" "$total" "$limit" >> "$INDEX_LOG"

if [ "$(structured_flag)" = "true" ]; then
  if ! run_structured_pipeline; then
    echo "DECISION: structured pipeline failed" >&2
    exit 1
  fi
fi

exit 0
