#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
mkdir -p runtime
INDEX_LOG="runtime/index.log"
count="$(find workspace -maxdepth 1 -type d -name 'project-*' | wc -l | tr -d ' ')"
timestamp() {
  date -Is
}
limit="${FACTORY_PROJECT_LIMIT:-12}"
if [ "$count" -gt "$limit" ]; then
  echo "REFLEX: EMERGENCY STOP (overflow protection)"
  printf '%s REFLEX status=block count=%s limit=%s\n' "$(timestamp)" "$count" "$limit" >> "$INDEX_LOG"
  exit 1
fi

printf '%s REFLEX status=pass count=%s limit=%s\n' "$(timestamp)" "$count" "$limit" >> "$INDEX_LOG"
exit 0
