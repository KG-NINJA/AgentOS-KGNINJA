#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

mkdir -p \
  queue/incoming \
  queue/leased \
  queue/processing \
  queue/done \
  queue/failed \
  repair_queue \
  runtime \
  runtime/logs \
  runtime/pid \
  runtime/tmp \
  runtime/factory_os \
  runtime/repair \
  runtime/repair_queue \
  runtime/repair_queue/incoming \
  runtime/repair_queue/leased \
  runtime/repair_queue/done \
  runtime/repair_queue/failed \
  runtime/repair_queue/results \
  runtime/repair_queue/logs

if [ ! -f runtime/metrics.jsonl ]; then
  : > runtime/metrics.jsonl
fi

if [ ! -f runtime/task_state.json ]; then
  printf '{}\n' > runtime/task_state.json
fi
