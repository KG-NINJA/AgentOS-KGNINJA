#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LEASED="$ROOT/queue/leased"
INCOMING="$ROOT/queue/incoming"
TIMEOUT="${LEASE_TIMEOUT_SEC:-1800}"

mkdir -p "$INCOMING" "$LEASED"

now="$(date +%s)"

for f in "$LEASED"/*.md; do
  [ -e "$f" ] || continue

  mtime="$(stat -c %Y "$f")"
  age=$(( now - mtime ))
  if [ "$age" -le "$TIMEOUT" ]; then
    continue
  fi

  base="$(basename "$f")"
  job_id="${base%.md}"
  dst="$INCOMING/$base"
  if [ -e "$dst" ]; then
    dst="$INCOMING/${job_id}_rescued_$(date -u +%Y%m%dT%H%M%SZ).md"
  fi

  mv "$f" "$dst"
  echo "[RESCUE] $(basename "$dst") age=$age"

  if [ -f "$ROOT/runtime/task_state.json" ]; then
    python3 "$ROOT/factory/state/task_state.py" --root "$ROOT" set-state "$job_id" queued >/dev/null 2>&1 || true
  fi
done
