#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <target_dir> [failure_log]" >&2
  exit 2
fi

TARGET_DIR="$1"
FAIL_LOG="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPROVAL_POLICY="${APPROVAL_POLICY:-untrusted}"
SANDBOX_MODE="${SANDBOX_MODE:-workspace-write}"
MAX_LOG_LINES="${MAX_LOG_LINES:-200}"
REPAIR_TIMEOUT_SEC="${REPAIR_TIMEOUT_SEC:-300}"
REPAIR_LOG_DIR="${ROOT}/runtime/repair"
REPAIR_LOG_FILE="${REPAIR_LOG_DIR}/codex_fix_$(date -u +%Y%m%dT%H%M%SZ).log"
APP_SERVER_CONTROL="${ROOT}/factory/agent/app_server_control.sh"
APP_SERVER_CLIENT="${ROOT}/factory/agent/codex_app_client.py"

REQ_ROOT="${ROOT}/runtime/repair_queue"
REQ_INCOMING="${REQ_ROOT}/incoming"
REQ_RESULTS="${REQ_ROOT}/results"
REQ_LOGS="${REQ_ROOT}/logs"
DAEMON_PID_FILE="${REQ_ROOT}/daemon.pid"

mkdir -p "$REPAIR_LOG_DIR" "$REQ_INCOMING" "$REQ_RESULTS" "$REQ_LOGS"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found" | tee -a "$REPAIR_LOG_FILE" >&2
  exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
  echo "target directory not found: $TARGET_DIR" | tee -a "$REPAIR_LOG_FILE" >&2
  exit 1
fi

failure_context="(no failure log provided)"
if [ -n "$FAIL_LOG" ] && [ -f "$FAIL_LOG" ]; then
  failure_context="$(tail -n "$MAX_LOG_LINES" "$FAIL_LOG")"
fi

if [ -x "$APP_SERVER_CONTROL" ] && [ -f "$APP_SERVER_CLIENT" ] && bash "$APP_SERVER_CONTROL" status >/dev/null 2>&1; then
  echo "[codex_fix] app-server mode target=$TARGET_DIR timeout=${REPAIR_TIMEOUT_SEC}s" | tee -a "$REPAIR_LOG_FILE"
  set +e
  python3 "$APP_SERVER_CLIENT" repair \
    --root "$ROOT" \
    --target-dir "$TARGET_DIR" \
    --fail-log "$FAIL_LOG" \
    --app-id "${APP_ID:-default}" \
    --timeout "$REPAIR_TIMEOUT_SEC" \
    --approval-policy "$APPROVAL_POLICY" \
    --sandbox-mode "$SANDBOX_MODE" >>"$REPAIR_LOG_FILE" 2>&1
  app_rc=$?
  set -e
  if [ "$app_rc" -eq 0 ]; then
    echo "[codex_fix] app-server repair success" | tee -a "$REPAIR_LOG_FILE"
    exit 0
  fi
  if [ "$app_rc" -eq 124 ]; then
    echo "[codex_fix] app-server timeout fail_reason=codex-timeout" | tee -a "$REPAIR_LOG_FILE"
    exit 124
  fi
  echo "[codex_fix] app-server repair failed; fallback to direct exec" | tee -a "$REPAIR_LOG_FILE"
fi

daemon_alive=0
if [ -f "$DAEMON_PID_FILE" ]; then
  daemon_pid="$(cat "$DAEMON_PID_FILE" 2>/dev/null || true)"
  if [ -n "${daemon_pid:-}" ] && kill -0 "$daemon_pid" 2>/dev/null; then
    if ps -p "$daemon_pid" -o args= 2>/dev/null | grep -q "factory/agent/codex_daemon.py"; then
      daemon_alive=1
    fi
  fi
fi

request_id="${JOB_ID:-repair}_$(date -u +%s)_$RANDOM"
result_path="${REQ_RESULTS}/${request_id}.json"

if [ "$daemon_alive" -eq 1 ]; then
  req_tmp="${REQ_INCOMING}/${request_id}.json.tmp"
  req_file="${REQ_INCOMING}/${request_id}.json"
  python3 - <<'PY' "$req_tmp" "$request_id" "${JOB_ID:-$request_id}" "${APP_ID:-}" "$TARGET_DIR" "$FAIL_LOG" "$ROOT"
import json,sys
out,request_id,job_id,app_id,target_dir,validate_log,root = sys.argv[1:]
payload = {
  "request_id": request_id,
  "job_id": job_id,
  "app_id": app_id or None,
  "target_dir": target_dir,
  "validate_log_path": validate_log,
  "constraints": {
    "allow_write_root": target_dir,
    "forbid_paths": [
      f"{root}/.git",
      f"{root}/.codex",
      f"{root}/runtime"
    ],
  },
}
with open(out, "w", encoding="utf-8") as fh:
  json.dump(payload, fh, ensure_ascii=False, indent=2)
  fh.write("\n")
PY
  mv "$req_tmp" "$req_file"
  echo "[codex_fix] queued daemon request: $req_file" | tee -a "$REPAIR_LOG_FILE"

  deadline=$(( $(date +%s) + REPAIR_TIMEOUT_SEC ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ -f "$result_path" ]; then
      status="$(python3 - <<'PY' "$result_path"
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
print(obj.get("status","failed"))
PY
)"
      if [ "$status" = "ok" ]; then
        echo "[codex_fix] daemon repair success: $result_path" | tee -a "$REPAIR_LOG_FILE"
        exit 0
      fi
      reason="$(python3 - <<'PY' "$result_path"
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
print(obj.get("fail_reason","repair-failed"))
PY
)"
      echo "[codex_fix] daemon repair failed: $reason" | tee -a "$REPAIR_LOG_FILE"
      exit 1
    fi
    sleep 1
  done
  echo "[codex_fix] daemon repair timeout; fallback to direct exec" | tee -a "$REPAIR_LOG_FILE"
fi

{
  echo "[codex_fix] direct mode target=$TARGET_DIR approval=$APPROVAL_POLICY sandbox=$SANDBOX_MODE"
  echo "[codex_fix] failure_log=${FAIL_LOG:-none}"
} | tee -a "$REPAIR_LOG_FILE"

cat <<EOF | codex -a "$APPROVAL_POLICY" exec --sandbox "$SANDBOX_MODE" -C "$TARGET_DIR" - >>"$REPAIR_LOG_FILE" 2>&1
You are a repair-only agent.

Task:
- Fix failing validation in this project directory.

Strict constraints:
1) Apply the minimum diff required to pass validation.
2) Do NOT modify files outside this working directory.
3) Do NOT regenerate the whole project.
4) Keep existing architecture and file layout.
5) If uncertain, prefer tiny, conservative fixes.

Validation failure context:
${failure_context}
EOF

rc=$?
echo "[codex_fix] direct rc=$rc log=$REPAIR_LOG_FILE" | tee -a "$REPAIR_LOG_FILE"
exit "$rc"
