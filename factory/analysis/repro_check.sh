#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

MANIFEST_FILE="runtime/experiment_manifest.json"
RESULTS_FILE="runtime/sweep_results.json"

if [ ! -f "$MANIFEST_FILE" ]; then
  echo "FAIL: missing runtime/experiment_manifest.json"
  exit 1
fi

if [ ! -f "$RESULTS_FILE" ]; then
  echo "FAIL: missing runtime/sweep_results.json"
  exit 1
fi

stored_hash="$(python3 - "$MANIFEST_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    data = json.load(f)
print(data.get('sweep_results_sha256', ''))
PY
)"

if [ -z "$stored_hash" ]; then
  echo "FAIL: sweep_results_sha256 not found in manifest"
  exit 1
fi

current_hash="$(python3 - "$RESULTS_FILE" <<'PY'
import hashlib
import sys

sha = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for chunk in iter(lambda: f.read(65536), b''):
        sha.update(chunk)
print(sha.hexdigest())
PY
)"

if [ "$stored_hash" = "$current_hash" ]; then
  echo "PASS: sweep_results hash matches manifest"
  exit 0
fi

echo "FAIL: sweep_results hash mismatch"
echo "stored=$stored_hash"
echo "current=$current_hash"
exit 1
