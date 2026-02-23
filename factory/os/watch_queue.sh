#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUEUE_ROOT="${QUEUE_ROOT:-$ROOT/queue}"
INCOMING_DIR="$QUEUE_ROOT/incoming"
RUNTIME_DIR="$ROOT/runtime"
LOG_DIR="$RUNTIME_DIR/factory_os"
LOCK_FILE="$LOG_DIR/watch_queue.lock"
POLL_SEC="${POLL_SEC:-5}"

LEASE_SCRIPT="$ROOT/factory/queue/lease_one.sh"
RUN_JOB_SCRIPT="$ROOT/factory/os/run_job.sh"
RESCUE_SCRIPT="$ROOT/factory/queue/rescue_leases.sh"
STATE_TOOL="$ROOT/factory/state/task_state.py"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(timestamp)] $*" | tee -a "$LOG_DIR/watch_queue.log"; }

trap 'rc=$?; log "watch_queue exit rc=$rc"' EXIT

mkdir -p "$INCOMING_DIR" "$LOG_DIR"

if ! bash "$ROOT/factory/os/bootstrap_runtime.sh" >/dev/null 2>&1; then
  log "bootstrap_runtime failed"
fi
if ! bash "$ROOT/factory/queue/rescue_leases.sh" >/dev/null 2>&1; then
  log "initial lease rescue failed"
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "already running"
  exit 0
fi

process_available_jobs() {
  while true; do
    local leased rc
    set +e
    leased="$("$LEASE_SCRIPT" 2>/dev/null)"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      if [ "$rc" -eq 2 ]; then
        break
      fi
      if [ "$rc" -eq 3 ]; then
        sleep 1
        continue
      fi
      log "lease_one failed rc=$rc"
      sleep 1
      continue
    fi
    if [ -z "$leased" ]; then
      break
    fi
    local job_name job_id app_id
    job_name="$(basename "$leased")"
    job_id="${job_name%.md}"
    app_id="$(sed -nE 's/^[[:space:]]*app_id:[[:space:]]*([A-Za-z0-9._-]+)[[:space:]]*$/\1/p' "$leased" | head -n 1 || true)"
    if [ -f "$STATE_TOOL" ] && command -v python3 >/dev/null 2>&1; then
      python3 "$STATE_TOOL" --root "$ROOT" update \
        --job-id "$job_id" \
        --state leased \
        --attempts 0 \
        --app-id "${app_id:-}" \
        --max-fix "${MAX_FIX:-3}" >/dev/null 2>&1 || true
    fi
    log "leased job: $leased"
    bash "$RUN_JOB_SCRIPT" "$leased" >>"$LOG_DIR/watch_queue.log" 2>&1 || true
  done
}

run_rescue_once() {
  if [ -x "$RESCUE_SCRIPT" ]; then
    local output rc
    set +e
    output="$(bash "$RESCUE_SCRIPT" 2>&1)"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      log "rescue_leases failed rc=$rc"
    fi
    if [ -n "$output" ]; then
      while IFS= read -r line; do
        [ -n "$line" ] || continue
        log "$line"
      done <<<"$output"
    fi
  fi
}

log "watch_queue start incoming=$INCOMING_DIR"
run_rescue_once
process_available_jobs

if command -v inotifywait >/dev/null 2>&1; then
  log "mode=inotify"
  while true; do
    run_rescue_once
    inotifywait -q -e create -e moved_to "$INCOMING_DIR" >/dev/null 2>&1 || true
    process_available_jobs
  done
else
  log "mode=polling poll_sec=$POLL_SEC"
  while true; do
    run_rescue_once
    process_available_jobs
    sleep "$POLL_SEC"
  done
fi
