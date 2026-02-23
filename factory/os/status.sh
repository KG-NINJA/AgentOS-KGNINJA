#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

count_dir() {
  local dir="$1"
  if [ -d "$dir" ]; then
    ls "$dir" | wc -l
  else
    echo 0
  fi
}

service_state() {
  local pattern="$1"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo running
  else
    echo stopped
  fi
}

app_server_state() {
  if [ -x factory/agent/app_server_control.sh ] && bash factory/agent/app_server_control.sh status >/dev/null 2>&1; then
    echo running
  else
    echo stopped
  fi
}

api_server_state() {
  if [ -x factory/api/api_control.sh ] && bash factory/api/api_control.sh status >/dev/null 2>&1; then
    echo running
  else
    echo unavailable
  fi
}

active_session_count() {
  python3 - <<'PY'
import json
import os
import time
from pathlib import Path

path = Path("runtime/codex_sessions.json")
if not path.exists():
    print(0)
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print(0)
    raise SystemExit(0)

if not isinstance(data, dict):
    print(0)
    raise SystemExit(0)

max_idle = int(os.environ.get("SESSION_MAX_IDLE_SEC", "86400"))
now = int(time.time())
count = 0
for rec in data.values():
    if not isinstance(rec, dict):
        continue
    if not rec.get("session_id"):
        continue
    last_used = int(rec.get("last_used", 0) or 0)
    if max_idle > 0 and last_used > 0 and now - last_used > max_idle:
        continue
    count += 1
print(count)
PY
}

last_metrics_entry() {
  local line
  if [ ! -f runtime/metrics.jsonl ]; then
    echo none
    return
  fi

  line="$(tail -n 1 runtime/metrics.jsonl 2>/dev/null || true)"
  if [ -z "$line" ]; then
    echo none
  else
    echo "$line"
  fi
}

queue_incoming="$(count_dir queue/incoming)"
queue_leased="$(count_dir queue/leased)"
queue_done="$(count_dir queue/done)"
queue_failed="$(count_dir queue/failed)"
worker_state="$(service_state "factory/os/watch_queue.sh")"
codex_state="$(app_server_state)"
api_state="$(api_server_state)"
sessions="$(active_session_count)"
last_job="$(last_metrics_entry)"
codex_available=0
[ -x factory/agent/app_server_control.sh ] && codex_available=1

health="OK"
if [ "$worker_state" != "running" ]; then
  health="DEGRADED"
elif [ "$codex_available" -eq 1 ] && [ "$codex_state" != "running" ]; then
  health="DEGRADED"
fi

version_text="Factory OS 1.0 FINAL"
if [ -f VERSION ]; then
  version_text="$(cat VERSION)"
fi

echo "$version_text"
echo
echo "Queue:"
echo "  incoming: $queue_incoming"
echo "  leased:   $queue_leased"
echo "  failed:   $queue_failed"
echo "  done:     $queue_done"
echo
echo "Workers:"
echo "  watch_queue: $worker_state"
echo
echo "Codex:"
echo "  daemon: $codex_state"
echo
echo "API:"
echo "  $api_state"
echo
echo "Sessions:"
echo "  active: $sessions"
echo
echo "Last job:"
echo "  $last_job"
echo
echo "Health:"
echo "  $health"
