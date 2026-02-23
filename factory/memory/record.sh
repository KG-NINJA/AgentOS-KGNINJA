#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ "$#" -ne 2 ]; then
  echo "record.sh: expected 2 args: project_name idea_filename" >&2
  exit 1
fi

project_name="$1"
idea_filename="$2"

printf '%s %s %s\n' "$(date -Is)" "$project_name" "$idea_filename" >> runtime/memory.log
