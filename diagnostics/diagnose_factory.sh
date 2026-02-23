#!/usr/bin/env bash
# Run ./factory.sh run repeatedly, capture logs/artifacts, then build diagnostics summaries.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIAG_DIR="$ROOT_DIR/diagnostics"
RUN_DIR="$DIAG_DIR/runs"
RAW_JSONL="$DIAG_DIR/raw_runs.jsonl"
SUMMARY_JSON="$DIAG_DIR/factory_diagnostics.json"
SUMMARY_TXT="$DIAG_DIR/factory_diagnostics.txt"
SCHEMA_PATH="$DIAG_DIR/factory_diagnostics.schema.json"

# Configurable run count and tail length. Defaults are safe for local diagnostics.
RUN_COUNT="${1:-8}"
TAIL_LINES="${2:-80}"

if ! [[ "$RUN_COUNT" =~ ^[0-9]+$ ]] || [ "$RUN_COUNT" -le 0 ]; then
  echo "RUN_COUNT must be a positive integer (got: $RUN_COUNT)" >&2
  exit 1
fi

if ! [[ "$TAIL_LINES" =~ ^[0-9]+$ ]] || [ "$TAIL_LINES" -le 0 ]; then
  echo "TAIL_LINES must be a positive integer (got: $TAIL_LINES)" >&2
  exit 1
fi

mkdir -p "$RUN_DIR"
: > "$RAW_JSONL"

cd "$ROOT_DIR"

if [ ! -x "./factory.sh" ]; then
  echo "Missing executable ./factory.sh in $ROOT_DIR" >&2
  echo "Hint: chmod +x factory.sh" >&2
  exit 1
fi

for i in $(seq 1 "$RUN_COUNT"); do
  run_id="run_$(printf '%03d' "$i")"
  stdout_file="$RUN_DIR/${run_id}.stdout.log"
  stderr_file="$RUN_DIR/${run_id}.stderr.log"
  index_tail_file="$RUN_DIR/${run_id}.index.tail.log"
  activity_tail_file="$RUN_DIR/${run_id}.activity.tail.log"

  # Execute the target command and capture stdout/stderr separately.
  set +e
  ./factory.sh run >"$stdout_file" 2>"$stderr_file"
  exit_status=$?
  set -e

  # Capture last N lines of runtime logs if present; otherwise leave a placeholder message.
  if [ -f "runtime/index.log" ]; then
    tail -n "$TAIL_LINES" "runtime/index.log" > "$index_tail_file"
  else
    printf '[missing] runtime/index.log\n' > "$index_tail_file"
  fi

  if [ -f "runtime/activity.log" ]; then
    tail -n "$TAIL_LINES" "runtime/activity.log" > "$activity_tail_file"
  else
    printf '[missing] runtime/activity.log\n' > "$activity_tail_file"
  fi

  # Try to extract reason code from latest known lines.
  reason_code=""
  if grep -Eq 'reason=|reason_code=' "$index_tail_file"; then
    reason_code="$(grep -E 'reason=|reason_code=' "$index_tail_file" | tail -n 1 | sed -E 's/.*reason_code=([^ ]+).*/\1/; s/.*reason=([^ ]+).*/\1/')"
  elif grep -Eq 'reason=|reason_code=' "$activity_tail_file"; then
    reason_code="$(grep -E 'reason=|reason_code=' "$activity_tail_file" | tail -n 1 | sed -E 's/.*reason_code=([^ ]+).*/\1/; s/.*reason=([^ ]+).*/\1/')"
  fi

  python3 - "$RAW_JSONL" "$run_id" "$exit_status" "$stdout_file" "$stderr_file" "$index_tail_file" "$activity_tail_file" "$reason_code" <<'PY'
import json
import sys

raw_path, run_id, exit_status, stdout_path, stderr_path, index_tail_path, activity_tail_path, reason_code = sys.argv[1:]
entry = {
    "run_id": run_id,
    "exit_status": int(exit_status),
    "stdout_path": stdout_path,
    "stderr_path": stderr_path,
    "index_tail_path": index_tail_path,
    "activity_tail_path": activity_tail_path,
    "reason_code": reason_code,
}
with open(raw_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY

done

# Build structured and human-readable diagnostics from raw run artifacts.
python3 "$DIAG_DIR/log_diagnoser.py" \
  --raw-jsonl "$RAW_JSONL" \
  --output-json "$SUMMARY_JSON" \
  --output-text "$SUMMARY_TXT" \
  --schema "$SCHEMA_PATH"

echo "Diagnostics JSON: $SUMMARY_JSON"
echo "Diagnostics TXT : $SUMMARY_TXT"
