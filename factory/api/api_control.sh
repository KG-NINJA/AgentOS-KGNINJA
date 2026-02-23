#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PID_FILE="runtime/api.pid"
LOG_FILE="runtime/api.log"

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
  if ! ps -p "$pid" -o args= 2>/dev/null | grep -q "factory/api/server.py"; then
    return 1
  fi
  return 0
}

start_server() {
  bash factory/os/bootstrap_runtime.sh >/dev/null 2>&1 || true
  if is_running; then
    echo "running"
    return 0
  fi
  nohup python3 factory/api/server.py >"$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  sleep 1
  if is_running; then
    echo "started"
  else
    echo "failed" >&2
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

case "${1:-status}" in
  start) start_server ;;
  stop) stop_server ;;
  status) status_server ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
