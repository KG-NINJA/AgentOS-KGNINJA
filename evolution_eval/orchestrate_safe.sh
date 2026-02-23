#!/usr/bin/env bash
# Safe batch orchestrator with preflight + validity gates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.json"
START_EPOCH="$(date +%s)"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Missing config: $CONFIG_FILE" >&2
  exit 1
fi

mapfile -t CFG < <(
  python3 - "$CONFIG_FILE" "$ROOT_DIR" <<'PY'
import json
import os
import sys

cfg_path, root = sys.argv[1], sys.argv[2]
cfg = json.load(open(cfg_path, 'r', encoding='utf-8'))
out_dir = cfg['output_dir']
if not os.path.isabs(out_dir):
    out_dir = os.path.join(root, out_dir)
print(int(cfg.get('batch_size', 5)))
print(int(cfg.get('max_batches', 3)))
print(int(cfg.get('max_duration_seconds', 3600)))
print(out_dir)
print(cfg['logs'].get('batch_log', 'safe_batches.log'))
print('true' if bool(cfg.get('preflight', {}).get('enabled', True)) else 'false')
print(int(cfg.get('preflight', {}).get('runs', 3)))
print(cfg['logs'].get('preflight_log', 'preflight.log'))
print(cfg['logs'].get('validity_log', 'validity.log'))
PY
)

BATCH_SIZE="${CFG[0]}"
MAX_BATCHES="${CFG[1]}"
MAX_DURATION_SECONDS="${CFG[2]}"
OUT_DIR="${CFG[3]}"
BATCH_LOG="$OUT_DIR/${CFG[4]}"
PREFLIGHT_ENABLED="${CFG[5]}"
PREFLIGHT_RUNS="${CFG[6]}"
PREFLIGHT_LOG="$OUT_DIR/${CFG[7]}"
VALIDITY_LOG="$OUT_DIR/${CFG[8]}"

mkdir -p "$OUT_DIR"
: > "$BATCH_LOG"
: > "$PREFLIGHT_LOG"
: > "$VALIDITY_LOG"

log_batch() {
  local batch_no="$1"
  local message="$2"
  printf '%s batch=%s %s\n' "$(date -Is)" "$batch_no" "$message" | tee -a "$BATCH_LOG"
}

if [ "$PREFLIGHT_ENABLED" = "true" ]; then
  log_batch "preflight" "start runs=$PREFLIGHT_RUNS"
  RUN_COUNT_OVERRIDE="$PREFLIGHT_RUNS" APPEND_LOGS=0 bash "$SCRIPT_DIR/baseline_runner.sh"
  preflight_json="$(python3 "$SCRIPT_DIR/preflight_gate.py")"
  echo "$preflight_json" > "$PREFLIGHT_LOG"
  pre_stop="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("should_stop") else "false")' <<< "$preflight_json")"
  pre_reason="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason", ""))' <<< "$preflight_json")"
  log_batch "preflight" "completed should_stop=$pre_stop reason=$pre_reason"
  if [ "$pre_stop" = "true" ]; then
    log_batch "preflight" "stop due to dependency errors"
    exit 0
  fi
fi

for batch in $(seq 1 "$MAX_BATCHES"); do
  now_epoch="$(date +%s)"
  elapsed=$((now_epoch - START_EPOCH))

  if [ "$elapsed" -gt "$MAX_DURATION_SECONDS" ]; then
    log_batch "$batch" "stop reason=max_duration_exceeded elapsed=${elapsed}s"
    exit 0
  fi

  log_batch "$batch" "start batch_size=$BATCH_SIZE elapsed=${elapsed}s"

  RUN_COUNT_OVERRIDE="$BATCH_SIZE" APPEND_LOGS=1 bash "$SCRIPT_DIR/baseline_runner.sh"
  RUN_COUNT_OVERRIDE="$BATCH_SIZE" APPEND_LOGS=1 bash "$SCRIPT_DIR/kernel_runner.sh"

  python3 "$SCRIPT_DIR/analyze.py"

  validity_json="$(python3 "$SCRIPT_DIR/validity_gate.py")"
  echo "$validity_json" > "$VALIDITY_LOG"
  valid="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("is_valid") else "false")' <<< "$validity_json")"
  valid_reason="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason", ""))' <<< "$validity_json")"
  log_batch "$batch" "validity is_valid=$valid reason=$valid_reason"

  if [ "$valid" != "true" ]; then
    log_batch "$batch" "stop invalid experiment conditions"
    exit 0
  fi

  safety_json="$(python3 "$SCRIPT_DIR/safety_controller.py" --start-epoch "$START_EPOCH")"
  echo "$safety_json" > "$OUT_DIR/safety_last.json"

  should_stop="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("should_stop") else "false")' <<< "$safety_json")"
  reason="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason", ""))' <<< "$safety_json")"
  abs_improvement="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("diagnostics", {}).get("absolute_improvement", 0.0))' <<< "$safety_json")"

  log_batch "$batch" "completed should_stop=$should_stop reason=$reason absolute_improvement=$abs_improvement"

  if [ "$should_stop" = "true" ]; then
    log_batch "$batch" "safe stop triggered"
    exit 0
  fi
done

log_batch "$MAX_BATCHES" "max_batches_reached"
