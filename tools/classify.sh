#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p workspace/docs workspace/core

is_doc() {
  case "${1,,}" in
    *.md|*.txt|*.rst|*.adoc|*.pdf) return 0 ;;
    *) return 1 ;;
  esac
}

is_core() {
  case "${1,,}" in
    *.json|*.yaml|*.yml|*.toml|*.ini|*.cfg|*.sh) return 0 ;;
    *) return 1 ;;
  esac
}

moved_docs=0
moved_core=0
while IFS= read -r -d '' file; do
  rel="${file#inbox/}"
  base="$(basename "$file")"

  # Skip tidy quarantine area and nested folders for safety.
  case "$rel" in
    _tidy/*|_tidy) continue ;;
  esac
  case "$rel" in
    */*) continue ;;
  esac

  if is_doc "$base"; then
    mv "$file" "workspace/docs/$base"
    moved_docs=$((moved_docs + 1))
  elif is_core "$base"; then
    mv "$file" "workspace/core/$base"
    moved_core=$((moved_core + 1))
  fi
done < <(find inbox -mindepth 1 -maxdepth 1 -type f -print0)

echo "classify: docs=$moved_docs core=$moved_core"
