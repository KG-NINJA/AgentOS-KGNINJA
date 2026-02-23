#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[THINK] parser start"

./factory/parser/run.sh

echo "[THINK] parser done"
