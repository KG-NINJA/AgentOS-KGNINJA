#!/usr/bin/env bash
# Run baseline iterations with deep artifacts for root-cause diagnostics.
set -euo pipefail

RUNNER="baseline"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.json"
LOGGER="$SCRIPT_DIR/run_logger.py"
CLEAN_SCRIPT="$SCRIPT_DIR/clean_workspace.sh"

load_config() {
  mapfile -t CFG < <(
    python3 - "$CONFIG_FILE" "$ROOT_DIR" "$RUNNER" <<'PY'
import json
import os
import shlex
import sys

cfg_path, root, runner = sys.argv[1], sys.argv[2], sys.argv[3]
with open(cfg_path, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
out = cfg['output_dir']
if not os.path.isabs(out):
    out = os.path.join(root, out)
print(int(cfg.get('num_runs', 5)))
print('true' if bool(cfg.get('workspace_cleanup', True)) else 'false')
print('true' if bool(cfg.get('debug', False)) else 'false')
print(out)
print(cfg['logs'][runner])
print(cfg.get('execution_command', './factory.sh run'))
print(int(cfg.get('decision_limit_override', 1000000)))
print(cfg['failure_definition'].get('decision_limit_reason_code', 'DECISION_LIMIT_EXCLUDED'))
print(shlex.split(cfg.get('execution_command', './factory.sh run'))[0])
inj = cfg.get('failure_injection', {})
print('true' if bool(inj.get('enabled', False)) else 'false')
print(str(inj.get('mode', 'dependency_error')))
print(float(inj.get('rate', 0.0)))
print(int(inj.get('seed', 0)))
print('true' if bool(inj.get('baseline_enabled', False)) else 'false')
PY
  )
}

command_available() {
  local first_token="$1"
  if [[ "$first_token" == ./* || "$first_token" == /* ]]; then
    [ -x "$ROOT_DIR/${first_token#./}" ] || [ -x "$first_token" ]
  else
    command -v "$first_token" >/dev/null 2>&1
  fi
}

load_config
NUM_RUNS="${CFG[0]}"
if [ -n "${RUN_COUNT_OVERRIDE:-}" ]; then
  NUM_RUNS="$RUN_COUNT_OVERRIDE"
fi
DO_CLEANUP="${CFG[1]}"
DEBUG_MODE="${CFG[2]}"
OUTPUT_DIR="${CFG[3]}"
LOG_FILE="$OUTPUT_DIR/${CFG[4]}"
EXEC_CMD="${CFG[5]}"
LIMIT_OVERRIDE="${CFG[6]}"
DECISION_LIMIT_REASON_CODE="${CFG[7]}"
FIRST_TOKEN="${CFG[8]}"
INJ_ENABLED="${CFG[9]}"
INJ_MODE="${CFG[10]}"
INJ_RATE="${CFG[11]}"
INJ_SEED="${CFG[12]}"
INJ_RUNNER_ENABLED="${CFG[13]}"

ARTIFACT_DIR="$OUTPUT_DIR/run_artifacts"
mkdir -p "$OUTPUT_DIR" "$ARTIFACT_DIR"
if [ "${APPEND_LOGS:-0}" != "1" ]; then
  : > "$LOG_FILE"
fi

if ! command_available "$FIRST_TOKEN"; then
  echo "[baseline] execution command not available: $EXEC_CMD"
  for i in $(seq 1 "$NUM_RUNS"); do
    artifact_file="$ARTIFACT_DIR/run_baseline_$(printf '%03d' "$i").json"
    python3 - "$artifact_file" <<'PY'
import json
import sys
payload = {
    "exit_code": 127,
    "decision_status": "unavailable",
    "post_gate_status": "unavailable",
    "stderr_tail": "execution command unavailable",
    "trace_tail": "",
    "trace_stage": "brain",
    "trace_reason": "command_unavailable"
}
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY
    python3 "$LOGGER" --log-file "$LOG_FILE" --run-id "baseline-${i}" --artifact-file "$artifact_file" --decision-limit-reason-code "$DECISION_LIMIT_REASON_CODE"
  done
  exit 0
fi

cd "$ROOT_DIR"
for i in $(seq 1 "$NUM_RUNS"); do
  if [ "$DO_CLEANUP" = "true" ]; then
    bash "$CLEAN_SCRIPT"
  fi

  run_id="baseline-$(printf '%03d' "$i")"
  stderr_file="/tmp/evolution_eval_${run_id}.stderr.log"
  stdout_file="/tmp/evolution_eval_${run_id}.stdout.log"
  : > "$stderr_file"
  : > "$stdout_file"

  set +e
  FACTORY_PROJECT_LIMIT="$LIMIT_OVERRIDE" FACTORY_TRACE_ENABLE="$([ "$DEBUG_MODE" = "true" ] && echo 1 || echo 0)" bash -lc "$EXEC_CMD" >"$stdout_file" 2>"$stderr_file"
  run_exit=$?
  set -e

  injection_applied="false"
  injection_mode=""
  if [ "$run_exit" -eq 0 ] && [ "$INJ_ENABLED" = "true" ] && [ "$INJ_RUNNER_ENABLED" = "true" ]; then
    inject_result="$(
      python3 - "$ROOT_DIR" "$run_id" "$INJ_MODE" "$INJ_RATE" "$INJ_SEED" <<'PY'
import hashlib
import json
import os
import random
import sys

root, run_id, mode, rate_s, seed_s = sys.argv[1:]
rate = float(rate_s)
seed = int(seed_s)
digest = hashlib.sha256(f"{seed}:{run_id}:{mode}".encode("utf-8")).hexdigest()
rng_seed = int(digest[:16], 16)
apply = random.Random(rng_seed).random() < rate

if not apply:
    print(json.dumps({"applied": False, "mode": "", "message": "skipped"}))
    raise SystemExit(0)

last_project_file = os.path.join(root, "runtime", ".last_generated_project")
if not os.path.exists(last_project_file):
    print(json.dumps({"applied": False, "mode": "", "message": "last_project_missing"}))
    raise SystemExit(0)

project_dir = open(last_project_file, "r", encoding="utf-8").read().strip()
pkg_path = os.path.join(root, project_dir, "core", "package.json")
if not os.path.exists(pkg_path):
    print(json.dumps({"applied": False, "mode": "", "message": "package_json_missing"}))
    raise SystemExit(0)

with open(pkg_path, "r", encoding="utf-8") as f:
    pkg = json.load(f)

if mode == "dependency_error":
    dep_digest = hashlib.sha256(f"dep:{run_id}:{seed}".encode("utf-8")).hexdigest()
    choose_invalid_dep = (int(dep_digest[:2], 16) % 2) == 0
    if choose_invalid_dep:
        deps = pkg.get("dependencies", {})
        if not isinstance(deps, dict):
            deps = {}
        deps["broken-dep"] = "9999.0.0"
        pkg["dependencies"] = deps
    else:
        scripts = pkg.get("scripts", {})
        if not isinstance(scripts, dict):
            scripts = {}
        scripts["test"] = "nonexistent-test-command-xyz"
        pkg["scripts"] = scripts

    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"applied": True, "mode": mode, "message": "applied"}))
else:
    print(json.dumps({"applied": False, "mode": "", "message": "unsupported_mode"}))
PY
    )"
    injection_applied="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("applied") else "false")' <<< "$inject_result")"
    injection_mode="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode",""))' <<< "$inject_result")"
  fi

  index_tail="$(tail -n 20 runtime/index.log 2>/dev/null || true)"
  trace_tail="$(tail -n 20 runtime/factory_exit_trace.log 2>/dev/null || true)"
  stderr_tail="$(tail -n 20 "$stderr_file" 2>/dev/null || true)"
  stdout_tail="$(tail -n 20 "$stdout_file" 2>/dev/null || true)"

  decision_status="$(printf '%s\n' "$index_tail" | grep 'DECISION status=' | tail -n 1 | sed -E 's/.*DECISION status=([^ ]+).*/\1/' || true)"
  post_gate_status="$(printf '%s\n' "$index_tail" | grep 'POST_GATE status=' | tail -n 1 | sed -E 's/.*POST_GATE status=([^ ]+).*/\1/' || true)"

  artifact_file="$ARTIFACT_DIR/run_${run_id}.json"
  python3 - "$artifact_file" "$run_exit" "$decision_status" "$post_gate_status" "$stderr_tail" "$stdout_tail" "$trace_tail" "$injection_applied" "$injection_mode" <<'PY'
import json
import sys

artifact_file, run_exit, decision_status, post_gate_status, stderr_tail, stdout_tail, trace_tail, injection_applied, injection_mode = sys.argv[1:]
trace_stage = ""
trace_reason = ""
for line in reversed(trace_tail.splitlines()):
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    trace_stage = str(obj.get("stage", ""))
    trace_reason = str(obj.get("reason", ""))
    break

payload = {
    "exit_code": int(run_exit),
    "decision_status": decision_status,
    "post_gate_status": post_gate_status,
    "stderr_tail": stderr_tail,
    "stdout_tail": stdout_tail,
    "trace_tail": trace_tail,
    "trace_stage": trace_stage,
    "trace_reason": trace_reason,
    "injection_applied": injection_applied.lower() == "true",
    "injection_mode": injection_mode if injection_mode else None,
}
with open(artifact_file, 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY

  python3 "$LOGGER" \
    --log-file "$LOG_FILE" \
    --run-id "$run_id" \
    --artifact-file "$artifact_file" \
    --decision-limit-reason-code "$DECISION_LIMIT_REASON_CODE"
done

echo "[baseline] complete: $LOG_FILE"
