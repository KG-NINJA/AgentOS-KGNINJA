#!/usr/bin/env bash
# Run factory generator diagnostics and produce structured JSON summaries.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUNS="${1:-5}"
OUT_DIR="diagnostics/output/generator_diag"
RUN_DIR="$OUT_DIR/runs"
SUMMARY_JSON="$OUT_DIR/generator_diag_summary.json"
RAW_JSONL="$OUT_DIR/raw_runs.jsonl"

mkdir -p "$RUN_DIR"
: > "$RAW_JSONL"

# Ensure log files exist so byte-offset slicing works.
mkdir -p runtime
: > runtime/activity.log
: > runtime/generator_stderr.log

extract_new_bytes() {
  local file="$1"
  local start="$2"
  local out="$3"
  local size
  size=$(wc -c < "$file" 2>/dev/null || echo 0)
  if [ "$size" -lt "$start" ]; then
    start=0
  fi
  local begin=$((start + 1))
  tail -c +"$begin" "$file" 2>/dev/null > "$out" || :
}

for i in $(seq 1 "$RUNS"); do
  run_id="run_$(printf '%03d' "$i")"

  activity_start=$(wc -c < runtime/activity.log 2>/dev/null || echo 0)
  gstderr_start=$(wc -c < runtime/generator_stderr.log 2>/dev/null || echo 0)

  run_stdout="$RUN_DIR/${run_id}.stdout.log"
  run_stderr="$RUN_DIR/${run_id}.stderr.log"

  set +e
  ./factory.sh run >"$run_stdout" 2>"$run_stderr"
  run_ec=$?
  set -e

  activity_delta="$RUN_DIR/${run_id}.activity.delta.log"
  gstderr_delta="$RUN_DIR/${run_id}.generator_stderr.delta.log"
  extract_new_bytes runtime/activity.log "$activity_start" "$activity_delta"
  extract_new_bytes runtime/generator_stderr.log "$gstderr_start" "$gstderr_delta"

  primary_rc=""
  fallback_attempted="false"
  fallback_invocation=""
  fallback_model=""
  fallback_rc=""
  final_exit_source=""
  explicit_exit_stage=""

  if grep -q 'GENERATOR_PRIMARY_RC=' "$activity_delta"; then
    primary_rc="$(grep 'GENERATOR_PRIMARY_RC=' "$activity_delta" | tail -n 1 | sed -E 's/.*GENERATOR_PRIMARY_RC=([-0-9]+).*/\1/')"
  fi

  if grep -q 'GENERATOR_FALLBACK_ATTEMPT_START' "$activity_delta"; then
    fallback_attempted="true"
  fi

  if grep -q 'GENERATOR_FALLBACK_INVOCATION=' "$activity_delta"; then
    fallback_invocation="$(grep 'GENERATOR_FALLBACK_INVOCATION=' "$activity_delta" | tail -n 1 | sed -E 's/.*GENERATOR_FALLBACK_INVOCATION=([^[:space:]]+).*/\1/')"
  fi

  if grep -q 'GENERATOR_FALLBACK_MODEL=' "$activity_delta"; then
    fallback_model="$(grep 'GENERATOR_FALLBACK_MODEL=' "$activity_delta" | tail -n 1 | sed -E 's/.*GENERATOR_FALLBACK_MODEL=([^[:space:]]+).*/\1/')"
  fi

  if grep -q 'GENERATOR_FALLBACK_RC=' "$activity_delta"; then
    fallback_rc="$(grep 'GENERATOR_FALLBACK_RC=' "$activity_delta" | tail -n 1 | sed -E 's/.*GENERATOR_FALLBACK_RC=([-0-9]+).*/\1/')"
  fi

  if grep -q 'GENERATOR_FINAL_EXIT_SOURCE=' "$activity_delta"; then
    final_exit_source="$(grep 'GENERATOR_FINAL_EXIT_SOURCE=' "$activity_delta" | tail -n 1 | sed -E 's/.*GENERATOR_FINAL_EXIT_SOURCE=([^[:space:]]+).*/\1/')"
  fi

  if grep -q 'GENERATOR_EXIT_CODE=1 stage=' "$activity_delta"; then
    explicit_exit_stage="$(grep 'GENERATOR_EXIT_CODE=1 stage=' "$activity_delta" | tail -n 1 | sed -E 's/.*stage=([^[:space:]]+).*/\1/')"
  fi

  # Keep exactly last 10 lines of generator stderr emitted during this run.
  stderr_tail=$(tail -n 10 "$gstderr_delta" 2>/dev/null || true)

  python3 - "$RUN_DIR/${run_id}.json" "$RAW_JSONL" "$run_id" "$run_ec" "$primary_rc" "$fallback_attempted" "$fallback_invocation" "$fallback_model" "$fallback_rc" "$final_exit_source" "$explicit_exit_stage" "$stderr_tail" <<'PY'
import json
import sys
from datetime import datetime, timezone

(
    out_file,
    raw_jsonl,
    run_id,
    run_ec,
    primary_rc,
    fallback_attempted,
    fallback_invocation,
    fallback_model,
    fallback_rc,
    final_exit_source,
    explicit_exit_stage,
    stderr_tail,
) = sys.argv[1:]

obj = {
    "run_id": run_id,
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "run_exit_code": int(run_ec),
    "generator_primary_rc": int(primary_rc) if primary_rc.strip() else None,
    "fallback_attempted": fallback_attempted.lower() == "true",
    "generator_fallback_invocation": fallback_invocation or None,
    "generator_fallback_model": fallback_model or None,
    "generator_fallback_rc": int(fallback_rc) if fallback_rc.strip() else None,
    "generator_final_exit_source": final_exit_source or None,
    "generator_explicit_exit_stage": explicit_exit_stage or None,
    "generator_stderr_tail_last10": stderr_tail.splitlines(),
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(obj, f, ensure_ascii=False, indent=2)
    f.write("\n")

with open(raw_jsonl, "a", encoding="utf-8") as f:
    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
PY

done

python3 diagnostics/analyze_generator_diag.py --input-dir "$RUN_DIR" --output-file "$SUMMARY_JSON"
echo "Generator diagnostics written: $SUMMARY_JSON"
