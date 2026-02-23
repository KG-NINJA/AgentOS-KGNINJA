#!/usr/bin/env bash
# Shared helpers for structured role scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

STRUCTURED_RUNTIME_DIR="${STRUCTURED_RUNTIME_DIR:-$ROOT/runtime}"
STRUCTURED_WORKSPACE_DIR="${STRUCTURED_WORKSPACE_DIR:-$ROOT/workspace}"
STRUCTURED_MEMORY_DIR="${STRUCTURED_MEMORY_DIR:-$ROOT/memory}"
STRUCTURED_LOG_FILE="${STRUCTURED_LOG_FILE:-$STRUCTURED_RUNTIME_DIR/structured_log.txt}"
STRUCTURED_FIXED_TIMESTAMP="${STRUCTURED_FIXED_TIMESTAMP:-}" # optional deterministic ts
GUARDRAIL_QUESTION_LINE="QUESTION: Does the queue markdown still assume docs/ARCHITECTURE.md is pre-existing?"

mkdir -p "$STRUCTURED_RUNTIME_DIR"
mkdir -p "$STRUCTURED_WORKSPACE_DIR"
mkdir -p "$STRUCTURED_MEMORY_DIR"
mkdir -p "$(dirname "$STRUCTURED_LOG_FILE")"

structured_timestamp() {
  if [ -n "$STRUCTURED_FIXED_TIMESTAMP" ]; then
    printf '%s' "$STRUCTURED_FIXED_TIMESTAMP"
  else
    TZ=UTC date -Is
  fi
}

structured_log() {
  local role="$1"
  local input_path="$2"
  local output_path="$3"
  local status="$4"
  local message="$5"
  printf '%s role=%s input=%s output=%s status=%s message="%s"\n' \
    "$(structured_timestamp)" "$role" "$input_path" "$output_path" "$status" "$message" >> "$STRUCTURED_LOG_FILE"
}

append_line_with_check() {
  local file="$1"
  local line="$2"
  ensure_parent_dir "$file"
  if [ -f "$file" ] && [ -s "$file" ]; then
    local last_hex
    last_hex="$(tail -c1 "$file" | od -An -tx1 | tr -d '[:space:]')"
    if [ -n "$last_hex" ] && [ "$last_hex" != "0a" ]; then
      printf '\n' >> "$file"
    fi
  fi
  if ! grep -Fxq "$line" "$file" 2>/dev/null; then
    printf '%s\n' "$line" >> "$file"
  fi
  if ! grep -Fxq "$line" "$file"; then
    echo "$ROLE_NAME: failed to append guardrail line to $file" >&2
    exit 1
  fi
}

append_guardrail_question() {
  append_line_with_check "$1" "$GUARDRAIL_QUESTION_LINE"
}

ensure_file_exists() {
  local path="$1"
  local label="$2"
  if [ ! -f "$path" ]; then
    structured_log "$ROLE_NAME" "$path" "" "error" "$label missing"
    echo "$ROLE_NAME: required file missing: $path" >&2
    exit 1
  fi
}

ensure_parent_dir() {
  local path="$1"
  local dir
  dir="$(dirname "$path")"
  mkdir -p "$dir"
}

sanitize_rel_path() {
  local raw="$1"
  local clean
  clean="${raw#./}"
  clean="${clean#/}"
  clean="${clean//../}"
  printf '%s' "$clean"
}

role_success() {
  structured_log "$ROLE_NAME" "$ROLE_INPUT" "$ROLE_OUTPUT" "success" "$1"
  echo "ROLE_COMPLETED=${ROLE_NAME^}"
}

setup_role_trap() {
  trap 'structured_log "$ROLE_NAME" "$ROLE_INPUT" "$ROLE_OUTPUT" "error" "${BASH_COMMAND}"' ERR
}
