#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <leased_job_file>" >&2
  exit 2
fi

LEASED_JOB="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUEUE_ROOT="${QUEUE_ROOT:-$ROOT/queue}"
LEASED_DIR="$QUEUE_ROOT/leased"
DONE_DIR="$QUEUE_ROOT/done"
FAILED_DIR="$QUEUE_ROOT/failed"
RUNTIME_DIR="$ROOT/runtime"
METRICS_FILE="$RUNTIME_DIR/metrics.jsonl"
METRICS_LOCK_FILE="$RUNTIME_DIR/metrics.lock"
MAX_FIX="${MAX_FIX:-3}"
FACTORY_CMD="${FACTORY_CMD:-bash ./factory.sh run}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-900}"

VALIDATE_SCRIPT="${VALIDATE_SCRIPT:-$ROOT/factory/repair/validate.sh}"
CODEX_FIX_SCRIPT="${CODEX_FIX_SCRIPT:-$ROOT/factory/repair/codex_fix.sh}"
PUSH_SCRIPT="${PUSH_SCRIPT:-$ROOT/factory_git_push.sh}"
STATE_TOOL="${STATE_TOOL:-$ROOT/factory/state/task_state.py}"

mkdir -p "$DONE_DIR" "$FAILED_DIR" "$RUNTIME_DIR"

if [ ! -f "$LEASED_JOB" ]; then
  echo "leased job not found: $LEASED_JOB" >&2
  exit 2
fi

job_name="$(basename "$LEASED_JOB")"
job_id="${job_name%.md}"
job_log="$RUNTIME_DIR/job_${job_id}_$(date -u +%Y%m%dT%H%M%SZ).log"
start_epoch="$(date +%s)"

status="failed"
fail_reason=""
attempts=0
deploy_url=""

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$job_log"
}

state_update() {
  local st="$1"
  shift || true
  if [ -f "$STATE_TOOL" ] && command -v python3 >/dev/null 2>&1; then
    python3 "$STATE_TOOL" --root "$ROOT" update \
      --job-id "$job_id" \
      --state "$st" \
      --attempts "$attempts" \
      --max-fix "$MAX_FIX" \
      --app-id "${APP_ID:-}" \
      --target-dir "${TARGET_DIR:-}" \
      "$@" >/dev/null 2>&1 || true
  fi
}

read_app_id() {
  local v
  v="$(sed -nE 's/^[[:space:]]*app_id:[[:space:]]*([A-Za-z0-9._-]+)[[:space:]]*$/\1/p' "$LEASED_JOB" | head -n 1 || true)"
  printf '%s' "$v"
}

latest_project_dir() {
  if [ -f "$ROOT/runtime/.last_generated_project" ]; then
    local rel
    rel="$(head -n 1 "$ROOT/runtime/.last_generated_project" | tr -d '\r')"
    if [ -n "$rel" ] && [ -d "$ROOT/$rel" ]; then
      printf '%s' "$ROOT/$rel"
      return 0
    fi
  fi
  ls -td "$ROOT"/workspace/project-* 2>/dev/null | head -n 1 || true
}

resolve_target_dir() {
  local app_id="$1"
  local generated latest_link app_dir
  generated="$(latest_project_dir)"
  latest_link="$ROOT/workspace/latest"

  if [ -n "$app_id" ]; then
    app_dir="$ROOT/workspace/apps/$app_id"
    mkdir -p "$ROOT/workspace/apps"
    if [ ! -d "$app_dir" ] && [ -n "$generated" ] && [ -d "$generated" ]; then
      cp -a "$generated" "$app_dir"
    else
      mkdir -p "$app_dir"
    fi
    printf '%s' "$app_dir"
    return 0
  fi

  if [ -L "$latest_link" ] || [ -d "$latest_link" ]; then
    printf '%s' "$latest_link"
    return 0
  fi
  if [ -n "$generated" ] && [ -d "$generated" ]; then
    ln -sfn "$generated" "$latest_link"
    printf '%s' "$latest_link"
    return 0
  fi
  printf '%s' "$ROOT/workspace"
}

finalize_job_file() {
  local dest_dir="$1"
  local dest="$dest_dir/$job_name"
  if [ -e "$dest" ]; then
    dest="$dest_dir/${job_id}_$(date -u +%Y%m%dT%H%M%SZ).md"
  fi
  mv "$LEASED_JOB" "$dest"
}

write_metric() {
  local duration
  duration=$(( $(date +%s) - start_epoch ))
  if [ ! -f "$METRICS_FILE" ]; then
    : > "$METRICS_FILE"
  fi
  exec 8>>"$METRICS_LOCK_FILE"
  flock -x 8
  python3 - <<'PY' "$METRICS_FILE" "$job_id" "${APP_ID:-}" "$status" "$attempts" "$duration" "$fail_reason" "$deploy_url"
import json,sys,time
out,job_id,app_id,status,attempts,duration,reason,deploy = sys.argv[1:]
row = {
  "ts": int(time.time()),
  "job_id": job_id,
  "app_id": app_id or None,
  "status": status,
  "attempts": int(attempts),
  "duration_sec": int(duration),
  "fail_reason": reason or None,
}
if deploy:
  row["deploy_url"] = deploy
with open(out, "a", encoding="utf-8") as f:
  f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
  flock -u 8
  exec 8>&-
}

APP_ID="$(read_app_id)"
log "job_id=$job_id app_id=${APP_ID:-none}"
state_update "running"

set +e
(cd "$ROOT" && timeout "$MAX_RUNTIME_SEC" bash -lc "$FACTORY_CMD" >>"$job_log" 2>&1)
factory_rc=$?
set -e
if [ "$factory_rc" -ne 0 ]; then
  if [ "$factory_rc" -eq 124 ]; then
    fail_reason="timeout"
  else
    fail_reason="factory-fail"
  fi
  log "factory failed"
  state_update "failed" --last-error "$fail_reason"
  finalize_job_file "$FAILED_DIR"
  write_metric
  exit 1
fi

TARGET_DIR="$(resolve_target_dir "$APP_ID")"
VALIDATE_LOG="$RUNTIME_DIR/validate_${job_id}.log"
log "target_dir=$TARGET_DIR"
state_update "validating"

if [ ! -x "$VALIDATE_SCRIPT" ]; then
  chmod +x "$VALIDATE_SCRIPT" || true
fi
if [ ! -x "$CODEX_FIX_SCRIPT" ]; then
  chmod +x "$CODEX_FIX_SCRIPT" || true
fi

if ! bash "$VALIDATE_SCRIPT" "$TARGET_DIR" "$VALIDATE_LOG" >>"$job_log" 2>&1; then
  attempts=1
  state_update "validating" --last-error "validate-fail"
  while [ "$attempts" -le "$MAX_FIX" ]; do
    log "validate failed, repair attempt $attempts/$MAX_FIX"
    state_update "repairing" --last-error "validate-fail"
    if ! JOB_ID="$job_id" APP_ID="${APP_ID:-}" bash "$CODEX_FIX_SCRIPT" "$TARGET_DIR" "$VALIDATE_LOG" >>"$job_log" 2>&1; then
      fail_reason="codex-fix-fail"
      log "codex fix failed"
      state_update "failed" --last-error "$fail_reason"
      finalize_job_file "$FAILED_DIR"
      write_metric
      exit 1
    fi
    state_update "validating"
    if bash "$VALIDATE_SCRIPT" "$TARGET_DIR" "$VALIDATE_LOG" >>"$job_log" 2>&1; then
      log "validate passed after repair"
      break
    fi
    attempts=$((attempts + 1))
  done
  if [ "$attempts" -gt "$MAX_FIX" ]; then
    attempts="$MAX_FIX"
    fail_reason="validate-fail"
    log "validate still failing after max attempts"
    state_update "failed" --last-error "$fail_reason"
    finalize_job_file "$FAILED_DIR"
    write_metric
    exit 1
  fi
else
  attempts=0
fi

if [ -x "$PUSH_SCRIPT" ]; then
  state_update "pushing"
  if ! (cd "$ROOT" && bash "$PUSH_SCRIPT" >>"$job_log" 2>&1); then
    fail_reason="push-fail"
    log "push failed"
    state_update "failed" --last-error "$fail_reason"
    finalize_job_file "$FAILED_DIR"
    write_metric
    exit 1
  fi
else
  log "push script missing, skip (stub)"
fi

status="success"
fail_reason="none"
state_update "done" --last-error ""
finalize_job_file "$DONE_DIR"
write_metric
log "job completed"
exit 0
