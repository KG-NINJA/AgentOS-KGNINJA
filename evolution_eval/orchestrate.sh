#!/usr/bin/env bash
# Run full experiment: baseline -> kernel -> analyze -> report.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.json"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Missing config file: $CONFIG_FILE" >&2
  exit 1
fi

mapfile -t CFG < <(
  python3 - "$CONFIG_FILE" "$ROOT_DIR" <<'PY'
import json
import os
import shlex
import sys

cfg_path, root = sys.argv[1], sys.argv[2]
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

cmd = cfg.get("execution_command", "./factory.sh run")
first = shlex.split(cmd)[0]
out_dir = cfg["output_dir"]
if not os.path.isabs(out_dir):
    out_dir = os.path.join(root, out_dir)

print(cmd)
print(first)
print(out_dir)
print(cfg["logs"]["analysis"])
PY
)

EXEC_CMD="${CFG[0]}"
FIRST_TOKEN="${CFG[1]}"
OUT_DIR="${CFG[2]}"
ANALYSIS_JSON="$OUT_DIR/${CFG[3]}"

command_available() {
  local first_token="$1"
  if [[ "$first_token" == ./* || "$first_token" == /* ]]; then
    [ -x "$ROOT_DIR/${first_token#./}" ] || [ -x "$first_token" ]
  else
    command -v "$first_token" >/dev/null 2>&1
  fi
}

if ! command_available "$FIRST_TOKEN"; then
  echo "[orchestrate] execution command unavailable: $EXEC_CMD"
  echo "[orchestrate] placeholder mode will be used by runners."
fi

echo "[orchestrate] baseline start"
bash "$SCRIPT_DIR/baseline_runner.sh"

echo "[orchestrate] kernel start"
bash "$SCRIPT_DIR/kernel_runner.sh"

echo "[orchestrate] analyze"
python3 "$SCRIPT_DIR/analyze.py"

echo "[orchestrate] report"
python3 "$SCRIPT_DIR/report.py"

if [ -f "$ANALYSIS_JSON" ]; then
  python3 - "$ANALYSIS_JSON" <<'PY'
import json
import sys
p = sys.argv[1]
with open(p, 'r', encoding='utf-8') as f:
    data = json.load(f)
b = data['baseline']['failure_rate']
k = data['kernel']['failure_rate']
if k < b:
    print('[orchestrate] RESULT: PASS (kernel failure rate improved)')
elif k == b:
    print('[orchestrate] RESULT: NO_CHANGE (kernel failure rate unchanged)')
else:
    print('[orchestrate] RESULT: FAIL (kernel failure rate regressed)')
PY
fi
