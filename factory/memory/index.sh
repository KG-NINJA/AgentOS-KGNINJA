#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
INDEX_LOG="runtime/index.log"
HISTORY_DIR="runtime/history"
HISTORY_LOG="$HISTORY_DIR/index.log"
workspace_count="$(find workspace -maxdepth 1 -type d -name 'project-*' | wc -l | tr -d ' ')"

if [ "$workspace_count" -eq 0 ]; then
  printf 'TOTAL_PROJECTS=0\n' > "$INDEX_LOG"
  exit 0
fi

if [ -s "$INDEX_LOG" ]; then
  mkdir -p "$HISTORY_DIR"
  {
    printf -- '--- %s ---\n' "$(date -Is)"
    cat "$INDEX_LOG"
  } >> "$HISTORY_LOG"
fi

last_line="$(tail -n 1 runtime/memory.log 2>/dev/null || true)"
last_project="$(echo "$last_line" | awk '{print $2}')"
last_idea="$(echo "$last_line" | awk '{print $3}')"

{
  printf 'TOTAL_PROJECTS=%s\n' "$workspace_count"
  if [ -n "$last_project" ]; then
    printf 'LAST_PROJECT=%s\n' "$last_project"
    printf 'LAST_IDEA=%s\n' "$last_idea"
  fi
} > "$INDEX_LOG"
