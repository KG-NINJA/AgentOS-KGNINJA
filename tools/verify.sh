#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ALLOWED=(
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

violations=()
for path in "$ROOT"/* "$ROOT"/.[!.]* "$ROOT"/..?*; do
  [ -e "$path" ] || continue
  name="$(basename "$path")"

  allowed=false
  for ok in "${ALLOWED[@]}"; do
    if [ "$name" = "$ok" ]; then
      allowed=true
      break
    fi
  done

  if [ "$allowed" = false ]; then
    violations+=("$name")
  fi
done

if [ "${#violations[@]}" -gt 0 ]; then
  printf 'verify: found disallowed top-level paths:\n' >&2
  for v in "${violations[@]}"; do
    printf ' - %s\n' "$v" >&2
  done
  printf '%s\n' "$(date -Is) disallowed: ${violations[*]}" >> "$ROOT/runtime/violations.log"
  exit 1
fi

echo "verify: OK"

if [ "${RUN_PARSER_TESTS:-0}" = "1" ]; then
  ./factory/parser/tests/test_interpret.sh
  ./factory/parser/tests/test_build_spec.sh
fi

if [ "${RUN_POST_GATE_TESTS:-0}" = "1" ]; then
  ./tools/test_post_generation_gate.sh
fi
