#!/usr/bin/env bash
# Quarantine prior workspace projects and reset runtime state for a clean experiment run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
QUARANTINE_DIR="$ROOT_DIR/runtime/quarantine/evolution_eval_$STAMP"

mkdir -p "$QUARANTINE_DIR/workspace" "$QUARANTINE_DIR/runtime" "$ROOT_DIR/runtime"

shopt -s nullglob
for project_dir in "$ROOT_DIR"/workspace/project-*; do
  mv "$project_dir" "$QUARANTINE_DIR/workspace/"
done
shopt -u nullglob

for runtime_file in index.log activity.log failure_summary.json publish.json .last_generated_project .quality_gate_meta; do
  if [ -e "$ROOT_DIR/runtime/$runtime_file" ]; then
    mv "$ROOT_DIR/runtime/$runtime_file" "$QUARANTINE_DIR/runtime/"
  fi
done

: > "$ROOT_DIR/runtime/index.log"
: > "$ROOT_DIR/runtime/activity.log"

echo "[evolution_eval] workspace cleaned; quarantine at $QUARANTINE_DIR"
