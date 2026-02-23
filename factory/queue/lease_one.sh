#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUEUE_ROOT="${QUEUE_ROOT:-$ROOT/queue}"
INCOMING_DIR="$QUEUE_ROOT/incoming"
LEASED_DIR="$QUEUE_ROOT/leased"
DONE_DIR="$QUEUE_ROOT/done"
FAILED_DIR="$QUEUE_ROOT/failed"
LOCK_FILE="$QUEUE_ROOT/.lease.lock"

mkdir -p "$INCOMING_DIR" "$LEASED_DIR" "$DONE_DIR" "$FAILED_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 3
fi

target="$(find "$INCOMING_DIR" -maxdepth 1 -type f -name '*.md' | sort | head -n 1 || true)"
if [ -z "$target" ]; then
  exit 2
fi

base="$(basename "$target")"
dest="$LEASED_DIR/$base"
if [ -e "$dest" ]; then
  stem="${base%.md}"
  dest="$LEASED_DIR/${stem}_$(date -u +%Y%m%dT%H%M%SZ).md"
fi

mv "$target" "$dest"
printf '%s\n' "$dest"
