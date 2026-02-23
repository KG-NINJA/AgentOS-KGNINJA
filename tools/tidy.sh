#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p inbox/_tidy

KEEP=(
  workspace
  inbox
  runtime
  cache
  system
  factory
  tools
  .githooks
  .git
  factory-cli
  factory.sh
  snapshots
)

moved=0
for path in "$ROOT"/* "$ROOT"/.[!.]* "$ROOT"/..?*; do
  [ -e "$path" ] || continue
  name="$(basename "$path")"

  keep=false
  for k in "${KEEP[@]}"; do
    if [ "$name" = "$k" ]; then
      keep=true
      break
    fi
  done

  if [ "$keep" = false ]; then
    target="inbox/_tidy/$name"
    if [ -e "$target" ]; then
      stamp="$(date +%Y%m%d%H%M%S)"
      target="inbox/_tidy/${name}.${stamp}"
    fi
    mv "$path" "$target"
    moved=$((moved + 1))
  fi
done

echo "tidy: moved $moved item(s)"
