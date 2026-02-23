#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
QUEUE_DIR="$ROOT/queue"

if [ ! -d "$QUEUE_DIR" ]; then
  exit 0
fi

shopt -s nullglob
queue_files=("$QUEUE_DIR"/*.md)
shopt -u nullglob

if [ "${#queue_files[@]}" -eq 0 ]; then
  exit 0
fi

echo "[PARSER] interpret"
./factory/parser/interpret.sh

echo "[PARSER] extract_entities"
./factory/parser/extract_entities.sh

echo "[PARSER] build_intent_ir"
./factory/parser/build_intent_ir.sh

echo "[PARSER] build_spec"
./factory/parser/build_spec.sh

echo "[PARSER] done"
