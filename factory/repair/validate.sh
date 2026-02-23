#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-}"
LOG_PATH="${2:-}"

if [ -z "$TARGET_DIR" ]; then
  echo "usage: $0 <target_dir> [log_path]" >&2
  exit 2
fi

if [ ! -d "$TARGET_DIR" ]; then
  echo "validate: target directory not found: $TARGET_DIR" >&2
  exit 2
fi

if [ -z "$LOG_PATH" ]; then
  LOG_PATH="$TARGET_DIR/.validate.log"
fi

mkdir -p "$(dirname "$LOG_PATH")"
: >"$LOG_PATH"

checks_run=0
failed=0

log() {
  printf '%s\n' "$*" | tee -a "$LOG_PATH"
}

run_check() {
  local name="$1"
  shift
  checks_run=$((checks_run + 1))
  log "== check: $name =="
  if "$@" >>"$LOG_PATH" 2>&1; then
    log "ok: $name"
  else
    log "fail: $name"
    failed=1
  fi
}

# Project-local validate script.
if [ -x "$TARGET_DIR/validate.sh" ]; then
  run_check "project-validate.sh" bash "$TARGET_DIR/validate.sh"
fi

# Node validations (if package scripts exist).
if [ -f "$TARGET_DIR/core/package.json" ] && command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if node -e 'const p=require(process.argv[1]); process.exit((p.scripts&&p.scripts.validate)?0:1)' "$TARGET_DIR/core/package.json" >/dev/null 2>&1; then
    run_check "node-validate-script" bash -lc "cd \"$TARGET_DIR/core\" && npm run -s validate"
  elif node -e 'const p=require(process.argv[1]); process.exit((p.scripts&&p.scripts.test)?0:1)' "$TARGET_DIR/core/package.json" >/dev/null 2>&1; then
    run_check "node-test-script" bash -lc "cd \"$TARGET_DIR/core\" && npm test --silent"
  fi
fi

# JS syntax checks (core/**/*.js)
if command -v node >/dev/null 2>&1; then
  while IFS= read -r jsfile; do
    [ -n "$jsfile" ] || continue
    run_check "node-check:$jsfile" node --check "$jsfile"
  done < <(find "$TARGET_DIR/core" -type f -name '*.js' 2>/dev/null | sort || true)
fi

# Python checks (if python files exist).
if command -v python3 >/dev/null 2>&1; then
  if find "$TARGET_DIR" -type f -name '*.py' 2>/dev/null | grep -q .; then
    run_check "python-compileall" python3 -m compileall -q "$TARGET_DIR"
    if [ -d "$TARGET_DIR/tests" ]; then
      run_check "python-unittest" bash -lc "cd \"$TARGET_DIR\" && python3 -m unittest discover -s tests -p 'test*.py'"
    fi
  fi
fi

if [ "$checks_run" -eq 0 ]; then
  log "validate: no checks executed (treated as pass)"
  exit 0
fi

if [ "$failed" -ne 0 ]; then
  log "validate: failed"
  exit 1
fi

log "validate: passed"
exit 0
