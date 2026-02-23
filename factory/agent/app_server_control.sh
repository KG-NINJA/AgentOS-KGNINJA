#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PID_FILE="runtime/app_server.pid"
LOG_FILE="runtime/app_server.log"
IN_FIFO="runtime/app_server.stdin"
OUT_FIFO="runtime/app_server.stdout"

ensure_runtime() {
  bash factory/os/bootstrap_runtime.sh >/dev/null 2>&1 || true
}

is_running() {
  if [ ! -f "$PID_FILE" ]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    return 1
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  if ! ps -p "$pid" -o args= 2>/dev/null | grep -q "codex app-server"; then
    return 1
  fi
  return 0
}

start_server() {
  ensure_runtime
  if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI not found" >&2
    exit 1
  fi

  if is_running; then
    echo "running"
    return 0
  fi

  rm -f "$PID_FILE"
  [ -p "$IN_FIFO" ] || mkfifo "$IN_FIFO"
  [ -p "$OUT_FIFO" ] || mkfifo "$OUT_FIFO"

  nohup bash -c '
    exec 3<>"$1"
    exec 4<>"$2"
    exec codex app-server --listen stdio:// <&3 >&4 2>>"$3"
  ' _ "$IN_FIFO" "$OUT_FIFO" "$LOG_FILE" >/dev/null 2>&1 &

  local pid=$!
  echo "$pid" > "$PID_FILE"
  sleep 1
  if is_running; then
    echo "started pid=$pid"
  else
    echo "failed to start app-server" >&2
    exit 1
  fi
}

stop_server() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "stopped"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$PID_FILE"
  echo "stopped"
}

status_server() {
  if is_running; then
    echo "running"
    return 0
  fi
  echo "stopped"
  return 1
}

cmd="${1:-status}"
case "$cmd" in
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  status)
    status_server
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
