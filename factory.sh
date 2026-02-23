#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MOBILE_MODE=0
MOBILE_MODE_INITIALIZED=0
MOBILE_MAX_ITERATIONS=0
MOBILE_MAX_RETRIES=0
MOBILE_TIMEOUT_SECONDS=0

TRACE_ENABLE="${FACTORY_TRACE_ENABLE:-0}"
TRACE_FILE="runtime/factory_exit_trace.log"
TRACE_STAGE="cli"
TRACE_REASON=""
TRACE_STDERR_FILE="$(mktemp /tmp/factory_stderr_trace.XXXXXX)"

mkdir -p "$ROOT/queue"
mkdir -p "$ROOT/runtime"
mkdir -p "$ROOT/workspace"
echo "[SHELL OK] Running under: ${SHELL:-unknown}"
echo "[BASH VERSION] ${BASH_VERSION:-unknown}"
RESCUE_SCRIPT="$ROOT/factory/queue/rescue_leases.sh"

enable_mobile_mode() {
  if [ "$MOBILE_MODE_INITIALIZED" -eq 1 ]; then
    return
  fi

  echo "[MOBILE MODE ENABLED]"
  MOBILE_MODE_INITIALIZED=1

  export MOBILE_MODE=1
  MOBILE_MAX_ITERATIONS=3
  MOBILE_MAX_RETRIES=1
  MOBILE_TIMEOUT_SECONDS=120

  export MAX_ITERATIONS="$MOBILE_MAX_ITERATIONS"
  export MAX_RETRIES="$MOBILE_MAX_RETRIES"
  export TIMEOUT_SECONDS="$MOBILE_TIMEOUT_SECONDS"
  export FACTORY_FORCE_TIMEOUT=1
  export FACTORY_TIMEOUT_SECONDS="$MOBILE_TIMEOUT_SECONDS"
  export NO_PUBLISH=1
  export DISABLE_HANDOFF=1
  export COST_BUDGET="low"
  export FORCE_NO_BACKGROUND_JOBS=1
  export LOG_TAG="mobile"

  echo "[MOBILE] Safe constraints applied" >> runtime/activity.log

  local guard_script="$ROOT/runtime/mobile_guard.sh"
  cat > "$guard_script" <<'GUARD'
#!/usr/bin/env bash
if [ "${MOBILE_MODE:-0}" = "1" ]; then
  case "$0" in
    */factory/publish/publish.sh)
      echo "[MOBILE] Publish step skipped" >&2
      exit 0
      ;;
    *handoff*)
      echo "[MOBILE] Handoff disabled" >&2
      exit 1
      ;;
  esac
fi
if [ -n "${FACTORY_MOBILE_PREV_BASH_ENV:-}" ] && [ -f "${FACTORY_MOBILE_PREV_BASH_ENV:-}" ]; then
  # shellcheck disable=SC1090
  source "$FACTORY_MOBILE_PREV_BASH_ENV"
fi
GUARD
  chmod +x "$guard_script"

  if [ -n "${BASH_ENV:-}" ]; then
    export FACTORY_MOBILE_PREV_BASH_ENV="$BASH_ENV"
  fi
  export BASH_ENV="$guard_script"
}

mobile_guarded_run() {
  local stage="$1"
  local reason="$2"
  shift 2

  if [ "$MOBILE_MODE" -ne 1 ]; then
    run_with_trace_capture "$stage" "$reason" "$@"
    return
  fi

  enable_mobile_mode

  local attempts=0
  while [ "$attempts" -lt "$MOBILE_MAX_ITERATIONS" ]; do
    attempts=$((attempts + 1))

    set +e
    run_with_trace_capture "$stage" "$reason" "$@"
    local rc=$?
    set -e

    if [ "$rc" -eq 124 ]; then
      echo "[MOBILE] Timeout after ${MOBILE_TIMEOUT_SECONDS}s" >&2
      return 124
    fi

    if [ "$rc" -eq 0 ]; then
      return 0
    fi

    if [ "$attempts" -ge "$MOBILE_MAX_ITERATIONS" ]; then
      echo "[MOBILE] Max iterations ($MOBILE_MAX_ITERATIONS) reached" >&2
      return "$rc"
    fi

    echo "[MOBILE] Retry $attempts/$MOBILE_MAX_ITERATIONS" >&2
  done

  return 1
}

infer_stage_reason_from_runtime() {
  local inferred_stage="brain"
  local inferred_reason="nonzero_exit"

  if [ -f runtime/index.log ]; then
    local tail_block
    tail_block="$(tail -n 80 runtime/index.log 2>/dev/null || true)"

    if printf '%s\n' "$tail_block" | grep -q 'POST_GATE status=fail'; then
      inferred_stage="post_gate"
      inferred_reason="post_gate_fail"
    elif printf '%s\n' "$tail_block" | grep -q 'QUALITY_GATE status=fail'; then
      inferred_stage="quality_gate"
      inferred_reason="quality_gate_fail"
    elif printf '%s\n' "$tail_block" | grep -q 'REFLEX status=block'; then
      inferred_stage="reflex"
      inferred_reason="reflex_block"
    elif printf '%s\n' "$tail_block" | grep -q 'DECISION status=block'; then
      inferred_stage="brain"
      inferred_reason="decision_limit_block"
    elif printf '%s\n' "$tail_block" | grep -q 'BOOTSTRAP stop_at=decision'; then
      inferred_stage="brain"
      inferred_reason="bootstrap_stop_decision"
    elif printf '%s\n' "$tail_block" | grep -q 'BOOTSTRAP stop_at=reflex'; then
      inferred_stage="reflex"
      inferred_reason="bootstrap_stop_reflex"
    elif printf '%s\n' "$tail_block" | grep -q 'DECISION status=pass'; then
      # Passed policy gates but still exited non-zero: most likely generator path failure.
      inferred_stage="generator"
      inferred_reason="generator_nonzero_after_policy_pass"
    fi
  fi

  printf '%s\n%s\n' "$inferred_stage" "$inferred_reason"
}

write_exit_trace() {
  local exit_code="$1"
  local stage="$2"
  local reason="$3"
  local stderr_tail
  stderr_tail="$(tail -n 5 "$TRACE_STDERR_FILE" 2>/dev/null || true)"

  python3 - "$TRACE_FILE" "$exit_code" "$stage" "$reason" "$stderr_tail" <<'PY'
import json
import sys
from datetime import datetime, timezone

trace_file, exit_code, stage, reason, stderr_tail = sys.argv[1:]
entry = {
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "exit_code": int(exit_code),
    "stage": stage,
    "reason": reason,
    "stderr_tail": stderr_tail,
}
with open(trace_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY
}

cleanup_trace() {
  local code="$?"

  if [ "$TRACE_ENABLE" = "1" ] && [ "$code" -ne 0 ]; then
    local stage="$TRACE_STAGE"
    local reason="$TRACE_REASON"

    if [ -z "$stage" ] || [ "$stage" = "cli" ] || { [ "$stage" = "brain" ] && [ "$reason" = "factory_cli_run" ]; }; then
      mapfile -t inferred < <(infer_stage_reason_from_runtime)
      stage="${inferred[0]}"
      reason="${inferred[1]}"
    fi

    if [ -z "$reason" ]; then
      reason="nonzero_exit"
    fi

    write_exit_trace "$code" "$stage" "$reason"
  fi

  rm -f "$TRACE_STDERR_FILE"
}

trap cleanup_trace EXIT

run_with_trace_capture() {
  local stage="$1"
  local reason="$2"
  shift 2

  TRACE_STAGE="$stage"
  TRACE_REASON="$reason"

  set +e
  local use_timeout="${FACTORY_FORCE_TIMEOUT:-0}"
  local timeout_seconds="${FACTORY_TIMEOUT_SECONDS:-0}"
  if [ "$use_timeout" = "1" ] && [ -n "$timeout_seconds" ] && [ "$timeout_seconds" -gt 0 ]; then
    timeout --preserve-status "$timeout_seconds" "$@" 2> >(tee -a "$TRACE_STDERR_FILE" >&2)
  else
    "$@" 2> >(tee -a "$TRACE_STDERR_FILE" >&2)
  fi
  local rc=$?
  set -e

  return "$rc"
}

count_items() {
  local dir="$1"
  if [ -d "$dir" ]; then
    ls "$dir" | wc -l
  else
    echo 0
  fi
}

rescue_stale_leases() {
  if [ -x "$RESCUE_SCRIPT" ]; then
    bash "$RESCUE_SCRIPT" >/dev/null 2>&1 || true
  fi
}

rescue_stale_leases_with_count() {
  if [ ! -x "$RESCUE_SCRIPT" ]; then
    echo 0
    return 0
  fi

  local count=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    if [[ "$line" == "[RESCUE]"* ]]; then
      count=$((count + 1))
    fi
  done < <(bash "$RESCUE_SCRIPT" 2>/dev/null || true)
  echo "$count"
}

is_proc_running() {
  local pattern="$1"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

watch_queue_running() {
  is_proc_running "factory/os/watch_queue.sh"
}

start_watch_queue_process() {
  local log_dir="$ROOT/runtime/factory_os"
  mkdir -p "$log_dir"
  nohup bash "$ROOT/factory/os/watch_queue.sh" >>"$log_dir/watch_queue.log" 2>&1 &
  sleep 0.2
}

ensure_watch_queue_service() {
  if watch_queue_running; then
    echo "running"
    return 0
  fi
  start_watch_queue_process
  if watch_queue_running; then
    echo "started"
  else
    echo "failed"
  fi
}

app_server_available() {
  [ -x "$ROOT/factory/agent/app_server_control.sh" ]
}

app_server_running() {
  if ! app_server_available; then
    return 1
  fi
  if bash "$ROOT/factory/agent/app_server_control.sh" status >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

ensure_app_server_service() {
  if ! app_server_available; then
    echo "skipped"
    return 0
  fi
  if bash "$ROOT/factory/agent/app_server_control.sh" status >/dev/null 2>&1; then
    echo "running"
    return 0
  fi
  if ! command -v codex >/dev/null 2>&1; then
    echo "skipped"
    return 0
  fi
  if bash "$ROOT/factory/agent/app_server_control.sh" start >/dev/null 2>&1; then
    echo "started"
  else
    echo "failed"
  fi
}

api_server_available() {
  [ -x "$ROOT/factory/api/api_control.sh" ]
}

api_server_running() {
  if ! api_server_available; then
    return 1
  fi
  if bash "$ROOT/factory/api/api_control.sh" status >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

ensure_api_server_running() {
  if ! api_server_available; then
    echo "skipped"
    return 0
  fi
  if bash "$ROOT/factory/api/api_control.sh" status >/dev/null 2>&1; then
    echo "running"
    return 0
  fi
  if bash "$ROOT/factory/api/api_control.sh" start >/dev/null 2>&1; then
    echo "started"
  else
    echo "failed"
  fi
}

stop_api_server_if_available() {
  if api_server_available; then
    bash "$ROOT/factory/api/api_control.sh" stop >/dev/null 2>&1 || true
  fi
}

stop_app_server_if_available() {
  if app_server_available; then
    bash "$ROOT/factory/agent/app_server_control.sh" stop >/dev/null 2>&1 || true
  fi
}

systemd_units_present() {
  local unit="$1"
  local systemd_dir="${HOME:-/home/user}/.config/systemd/user"
  [ -f "$systemd_dir/$unit" ]
}

start_systemd_units_if_present() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return
  fi
  if systemd_units_present "factory-os.service"; then
    systemctl --user start factory-os.service >/dev/null 2>&1 || true
  fi
  if systemd_units_present "codex-daemon.service"; then
    systemctl --user start codex-daemon.service >/dev/null 2>&1 || true
  fi
}

stop_systemd_units_if_present() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return
  fi
  if systemd_units_present "factory-os.service"; then
    systemctl --user stop factory-os.service >/dev/null 2>&1 || true
  fi
  if systemd_units_present "codex-daemon.service"; then
    systemctl --user stop codex-daemon.service >/dev/null 2>&1 || true
  fi
}

factory_health_snapshot() {
  local watch_state codex_state api_state health
  if watch_queue_running; then
    watch_state="running"
  else
    watch_state="stopped"
  fi

  if app_server_available && app_server_running; then
    codex_state="running"
  else
    codex_state="stopped"
  fi

  if ! api_server_available; then
    api_state="unavailable"
  elif api_server_running; then
    api_state="running"
  else
    api_state="unavailable"
  fi

  health="OK"
  if [ "$watch_state" != "running" ]; then
    health="DEGRADED"
  elif app_server_available && [ "$codex_state" != "running" ]; then
    health="DEGRADED"
  fi

  printf '%s %s %s %s\n' "$health" "$watch_state" "$codex_state" "$api_state"
}

print_health_summary() {
  read -r health watch_state codex_state api_state < <(factory_health_snapshot)
  echo "Health: $health"
  echo "  watch_queue=$watch_state codex=$codex_state api=$api_state"
}

factory_start() {
  bash "$ROOT/factory/os/bootstrap_runtime.sh" >/dev/null 2>&1 || true
  rescue_stale_leases

  local workers_ready=0 codex_ready=0 api_ready=0
  if watch_queue_running; then
    workers_ready=1
  fi
  if app_server_available; then
    if app_server_running; then
      codex_ready=1
    fi
  else
    codex_ready=1
  fi
  if api_server_available; then
    if api_server_running; then
      api_ready=1
    fi
  else
    api_ready=1
  fi

  if [ "$workers_ready" -eq 1 ] && [ "$codex_ready" -eq 1 ] && [ "$api_ready" -eq 1 ]; then
    echo "already running"
    print_health_summary
    return 0
  fi

  local watch_state app_state api_state
  watch_state="$(ensure_watch_queue_service)"
  app_state="$(ensure_app_server_service)"
  start_systemd_units_if_present
  api_state="$(ensure_api_server_running)"
  if [ "$watch_state" = "failed" ]; then
    echo "[WARN] watch_queue start failed" >&2
  fi
  if [ "$app_state" = "failed" ]; then
    echo "[WARN] app-server start failed" >&2
  fi
  if [ "$api_state" = "failed" ]; then
    echo "[WARN] API server start failed" >&2
  fi
  echo "Factory OS started"
  print_health_summary
}

factory_stop() {
  stop_api_server_if_available
  stop_app_server_if_available
  stop_systemd_units_if_present
  pkill -f "factory/os/watch_queue.sh" >/dev/null 2>&1 || true
  pkill -f "codex_daemon.py" >/dev/null 2>&1 || true
  echo "Factory OS stopped"
}

factory_restart() {
  factory_stop
  sleep 1
  factory_start
}

factory_repair() {
  bash "$ROOT/factory/os/bootstrap_runtime.sh" >/dev/null 2>&1 || true
  local rescue_count restart_count watch_state app_state api_state
  rescue_count="$(rescue_stale_leases_with_count)"

  restart_count=0
  if watch_queue_running; then
    watch_state="running"
  else
    watch_state="$(ensure_watch_queue_service)"
    if [ "$watch_state" = "started" ]; then
      restart_count=$((restart_count + 1))
    fi
  fi

  if app_server_available; then
    if app_server_running; then
      app_state="running"
    else
      app_state="$(ensure_app_server_service)"
      if [ "$app_state" = "started" ]; then
        restart_count=$((restart_count + 1))
      fi
    fi
  fi

  if api_server_available; then
    if api_server_running; then
      api_state="running"
    else
      api_state="$(ensure_api_server_running)"
      if [ "$api_state" = "started" ]; then
        restart_count=$((restart_count + 1))
      fi
    fi
  fi

  print_health_summary
  echo "leases rescued: $rescue_count"
  echo "workers restarted: $restart_count"
  echo "REPAIR OK"
}

cmd="${1:-}"
if [ -z "$cmd" ]; then
  echo "Factory CLI v1" >&2
  echo "Commands:" >&2
  echo "  run" >&2
  echo "  think" >&2
  echo "  status" >&2
  echo "  start" >&2
  echo "  stop" >&2
  echo "  restart" >&2
  echo "  repair" >&2
  echo "  version" >&2
  echo "  selftest" >&2
  echo "  snapshot [save|restore]" >&2
  echo "  inspect" >&2
  TRACE_STAGE="brain"
  TRACE_REASON="missing_command"
  exit 1
fi

shift || true

if [ "$cmd" = "run" ]; then
  mobile_filtered_args=()
  mobile_passthrough=0
  while [ "$#" -gt 0 ]; do
    if [ "$mobile_passthrough" -eq 1 ]; then
      mobile_filtered_args+=("$1")
      shift
      continue
    fi
    case "$1" in
      --mobile)
        MOBILE_MODE=1
        ;;
      --)
        mobile_passthrough=1
        mobile_filtered_args+=("$1")
        ;;
      *)
        mobile_filtered_args+=("$1")
        ;;
    esac
    shift
  done
  if [ "${#mobile_filtered_args[@]}" -gt 0 ]; then
    set -- "${mobile_filtered_args[@]}"
  else
    set --
  fi
fi

case "$cmd" in
  run)
    mobile_guarded_run "brain" "factory_cli_run" bash ./factory-cli/run.sh "$@"
    ;;
  think)
    run_with_trace_capture "brain" "factory_cli_think" bash ./factory-cli/think.sh "$@"
    ;;
  status)
    run_with_trace_capture "brain" "factory_cli_status" bash ./factory/os/status.sh "$@"
    ;;
  start)
    run_with_trace_capture "brain" "factory_cli_start" factory_start
    ;;
  stop)
    run_with_trace_capture "brain" "factory_cli_stop" factory_stop
    ;;
  restart)
    run_with_trace_capture "brain" "factory_cli_restart" factory_restart
    ;;
  repair)
    run_with_trace_capture "brain" "factory_cli_repair" factory_repair
    ;;
  version)
    if [ -f "$ROOT/VERSION" ]; then
      cat "$ROOT/VERSION"
    else
      echo "Factory OS 1.0 FINAL"
    fi
    ;;
  selftest)
    run_with_trace_capture "brain" "factory_cli_selftest" bash ./factory/selftest.sh "$@"
    ;;
  snapshot)
    run_with_trace_capture "brain" "factory_cli_snapshot" bash ./factory-cli/snapshot.sh "$@"
    ;;
  inspect)
    run_with_trace_capture "brain" "factory_cli_inspect" bash ./factory-cli/inspect.sh "$@"
    ;;
  *)
    echo "Factory CLI v1" >&2
    echo "Commands:" >&2
    echo "  run" >&2
    echo "  think" >&2
    echo "  status" >&2
    echo "  start" >&2
    echo "  stop" >&2
    echo "  restart" >&2
    echo "  repair" >&2
    echo "  version" >&2
    echo "  selftest" >&2
    echo "  snapshot [save|restore]" >&2
    echo "  inspect" >&2
    TRACE_STAGE="brain"
    TRACE_REASON="unknown_command"
    exit 1
    ;;
esac
