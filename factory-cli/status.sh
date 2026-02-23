#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
QUEUE_DIR="$ROOT/queue"

total="$(grep '^TOTAL_PROJECTS=' runtime/index.log 2>/dev/null | tail -n 1 | cut -d= -f2 || true)"
total="${total:-0}"

last_project="$(grep '^LAST_PROJECT=' runtime/index.log 2>/dev/null | tail -n 1 | cut -d= -f2 || true)"
last_project="${last_project:-unknown}"

if [ -d "$QUEUE_DIR" ]; then
  shopt -s nullglob
  queue_files=("$QUEUE_DIR"/*.md)
  shopt -u nullglob
  queue_count="${#queue_files[@]}"
else
  queue_count=0
fi

if [ -f runtime/spec.json ]; then
  spec_ready="yes"
else
  spec_ready="no"
fi

echo "[STATUS]"
echo "TOTAL_PROJECTS=$total"
echo "QUEUE_COUNT=$queue_count"
echo "SPEC_READY=$spec_ready"
echo "LAST_PROJECT=$last_project"
