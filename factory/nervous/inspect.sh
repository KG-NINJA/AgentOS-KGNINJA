#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

project_count="$(find workspace -mindepth 1 -maxdepth 1 -type d -name 'project-*' | wc -l | tr -d ' ')"
printf 'INSPECT: total_projects=%s\n' "$project_count" >> runtime/activity.log

if [ "$project_count" -gt 10 ]; then
  printf 'INSPECT WARNING: total_projects=%s exceeds threshold=10\n' "$project_count" >> runtime/violations.log
fi
