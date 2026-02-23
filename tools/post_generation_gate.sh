#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

RUNTIME_DIR="runtime"
SPEC_FILE="$RUNTIME_DIR/spec.json"
SUMMARY_FILE="$RUNTIME_DIR/failure_summary.json"
mkdir -p "$RUNTIME_DIR"

timestamp() {
  date -Is
}

first_meaningful_line() {
  local path="$1"
  python3 - <<'PY' "$path"
import sys
path = sys.argv[1]
for raw in open(path, encoding="utf-8", errors="replace"):
    s = raw.strip()
    if not s:
        continue
    # Skip npm script prefix/noise lines.
    if s.startswith("> "):
        continue
    print(s)
    break
PY
}

write_summary() {
  local status="$1"
  local reason_code="$2"
  local reason="$3"
  local project_dir="$4"
  local project_type="$5"
  local first_error_line="$6"
  local json_syntax="$7"
  local js_syntax="$8"
  local shell_syntax="$9"
  local npm_install="${10}"
  local require_check="${11}"
  local tests="${12}"
  python3 - <<'PY' "$SUMMARY_FILE" "$status" "$reason_code" "$reason" "$project_dir" "$project_type" "$first_error_line" "$json_syntax" "$js_syntax" "$shell_syntax" "$npm_install" "$require_check" "$tests"
import json
import sys
from datetime import datetime

(
    out_path,
    status,
    reason_code,
    reason,
    project_dir,
    project_type,
    first_error_line,
    json_syntax,
    js_syntax,
    shell_syntax,
    npm_install,
    require_check,
    tests,
) = sys.argv[1:]

payload = {
    "status": status,
    "phase": "post_generation_gate",
    "reason_code": reason_code,
    "reason": reason,
    "project_dir": project_dir,
    "project_type": project_type,
    "checks": {
        "json_syntax": json_syntax,
        "js_syntax": js_syntax,
        "shell_syntax": shell_syntax,
        "npm_install": npm_install,
        "require_check": require_check,
        "tests": tests,
    },
    "first_error_line": first_error_line,
    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
}

reason_code = payload.get("reason_code", "")
actions = []
if reason_code == "json_syntax_failed":
    actions = ["Fix JSON syntax in first_error_line file", "Regenerate project after fixing template/prompt"]
elif reason_code == "js_syntax_failed":
    actions = ["Run node --check on first_error_line file", "Fix malformed quotes/template literals and retry"]
elif reason_code == "shell_syntax_failed":
    actions = ["Run bash -n on failing shell file", "Fix heredoc/quote balance and retry"]
elif reason_code == "install_failed":
    actions = ["Check network/DNS to npm registry", "Run npm install manually in npm_root and inspect logs"]
elif reason_code == "require_check_failed":
    actions = ["Add missing dependency to package.json", "Run npm install then node -e require() verification"]
elif reason_code == "tests_failed":
    actions = ["Run npm test manually in npm_root", "Fix failing test or implementation mismatch"]
elif reason_code == "missing_required_file":
    actions = ["Ensure required files are generated", "Fix generator prompt/skill to include missing files"]
elif reason_code == "missing_python_file":
    actions = ["Ensure required desktop Python files are generated", "Regenerate with desktop_app-specific template and prompt"]
elif reason_code == "missing_dependency_decl":
    actions = ["Add missing dependency to requirements.txt", "Keep requirements aligned with imports and rerun gate"]
elif reason_code == "py_compile_failed":
    actions = ["Run python3 -m compileall core tests", "Fix syntax/import errors and rerun generation checks"]
elif reason_code == "pytest_failed":
    actions = ["Run python3 -m pytest -k diff --maxfail=1", "Align implementation with expected diff/logging behavior"]
elif reason_code == "contract_file_missing":
    actions = ["Ensure every spec.contracts.files entry is generated", "Fix router/generator contract mapping and rerun"]
elif reason_code == "contract_test_failed":
    actions = ["Run failing command from spec.validation_plan.test_commands manually", "Fix implementation to satisfy contract behavior/tests"]
payload["suggested_actions"] = actions

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

log_gate() {
  local status="$1"
  local reason="$2"
  local project="$3"
  printf '%s POST_GATE status=%s reason=%s project=%s\n' "$(timestamp)" "$status" "$reason" "$project" >> "$RUNTIME_DIR/index.log"
  printf '%s POST_GATE status=%s reason=%s project=%s\n' "$(timestamp)" "$status" "$reason" "$project" >> "$RUNTIME_DIR/activity.log"
}

fail_gate() {
  local reason_code="$1"
  local reason="$2"
  local first_error="${3:-}"
  local project_dir="$4"
  local project_type="$5"
  write_summary "fail" "$reason_code" "$reason" "$project_dir" "$project_type" "$first_error" \
    "${JSON_STATUS:-pending}" "${JS_STATUS:-pending}" "${SH_STATUS:-pending}" \
    "${INSTALL_STATUS:-pending}" "${REQUIRE_STATUS:-pending}" "${TEST_STATUS:-pending}"
  log_gate "fail" "$reason_code" "$project_dir"
  echo "POST_GATE: $reason" >&2
  exit 1
}

JSON_STATUS="pending"
JS_STATUS="pending"
SH_STATUS="pending"
INSTALL_STATUS="pending"
REQUIRE_STATUS="pending"
TEST_STATUS="pending"

project_type="unknown"
if command -v jq >/dev/null 2>&1 && [ -f "$SPEC_FILE" ]; then
  project_type="$(jq -r '.project_type // "unknown"' "$SPEC_FILE" 2>/dev/null || echo "unknown")"
fi

project_dir=""
if [ -f "$RUNTIME_DIR/.last_generated_project" ]; then
  project_dir="$(head -n 1 "$RUNTIME_DIR/.last_generated_project" | tr -d '\r')"
fi
if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
  project_dir="$(ls -dt workspace/project-* 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
  fail_gate "missing_project" "latest generated project not found" "" "unknown" "$project_type"
fi

# Contract-driven minimum files (Open MD pipeline).
if command -v jq >/dev/null 2>&1 && [ -f "$SPEC_FILE" ]; then
  while IFS= read -r cfile; do
    [ -n "$cfile" ] || continue
    if [ ! -f "$project_dir/$cfile" ]; then
      fail_gate "contract_file_missing" "contract file missing: $cfile" "$project_dir/$cfile" "$project_dir" "$project_type"
    fi
  done < <(jq -r '.contracts.files[]? // empty' "$SPEC_FILE" 2>/dev/null || true)
fi

if [ "$project_type" != "web_app" ] && [ "$project_type" != "desktop_app" ]; then
  JSON_STATUS="skipped"
  JS_STATUS="skipped"
  SH_STATUS="skipped"
  INSTALL_STATUS="skipped"
  REQUIRE_STATUS="skipped"
  TEST_STATUS="skipped"
  write_summary "pass" "non_web_app" "post-generation gate skipped for non web_app" "$project_dir" "$project_type" "" \
    "$JSON_STATUS" "$JS_STATUS" "$SH_STATUS" "$INSTALL_STATUS" "$REQUIRE_STATUS" "$TEST_STATUS"
  log_gate "pass" "non_web_app" "$project_dir"
  exit 0
fi

if [ "$project_type" = "desktop_app" ]; then
  JSON_STATUS="skipped"
  JS_STATUS="skipped"

  required_files=(
    "$project_dir/core/app.py"
    "$project_dir/core/ui/main_window.py"
    "$project_dir/core/services/diff_detector.py"
    "$project_dir/core/services/markdown_logger.py"
    "$project_dir/core/services/notifier.py"
    "$project_dir/requirements.txt"
    "$project_dir/tests/test_diff_detector.py"
    "$project_dir/docs/ARCHITECTURE.md"
  )
  for rf in "${required_files[@]}"; do
    if [ ! -f "$rf" ]; then
      fail_gate "missing_python_file" "required desktop file missing: $rf" "$rf" "$project_dir" "$project_type"
    fi
  done

  if [ ! -f "$project_dir/logs/.gitkeep" ] && [ ! -d "$project_dir/logs" ]; then
    fail_gate "missing_python_file" "desktop logs directory missing" "$project_dir/logs" "$project_dir" "$project_type"
  fi

  dep_missing=""
  for dep in PySide6 mss opencv-python scikit-image; do
    if ! grep -Eq "^${dep}([<>=!~].*)?$|^${dep}[[:space:]]*#" "$project_dir/requirements.txt"; then
      dep_missing="$dep"
      break
    fi
  done
  if [ -n "$dep_missing" ]; then
    INSTALL_STATUS="fail"
    fail_gate "missing_dependency_decl" "requirements.txt missing dependency: $dep_missing" "$project_dir/requirements.txt" "$project_dir" "$project_type"
  fi
  INSTALL_STATUS="pass"
  REQUIRE_STATUS="pass"

  sh_files="$(find "$project_dir" -type f -name '*.sh' 2>/dev/null || true)"
  if [ -n "$sh_files" ]; then
    while IFS= read -r shf; do
      [ -n "$shf" ] || continue
      if ! bash -n "$shf" >/dev/null 2>&1; then
        SH_STATUS="fail"
        fail_gate "shell_syntax_failed" "shell syntax check failed: $shf" "$shf" "$project_dir" "$project_type"
      fi
    done <<< "$sh_files"
    SH_STATUS="pass"
  else
    SH_STATUS="skipped"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    TEST_STATUS="fail"
    fail_gate "python_missing" "python3 is required for desktop_app gate checks" "python3 command not found" "$project_dir" "$project_type"
  fi

  if ! python3 -m compileall "$project_dir/core" "$project_dir/tests" >/dev/null 2>&1; then
    TEST_STATUS="fail"
    fail_gate "py_compile_failed" "python compile check failed" "$project_dir/core" "$project_dir" "$project_type"
  fi

  pytest_log="$(mktemp)"
  if ! (cd "$project_dir" && PYTHONPATH="$project_dir" python3 -m pytest -k diff --maxfail=1) >"$pytest_log" 2>&1; then
    TEST_STATUS="fail"
    first_line="$(first_meaningful_line "$pytest_log" | tr -d '\r')"
    if [ -z "$first_line" ]; then
      first_line="$(head -n 1 "$pytest_log" | tr -d '\r')"
    fi
    rm -f "$pytest_log"
    fail_gate "pytest_failed" "pytest failed at $project_dir" "$first_line" "$project_dir" "$project_type"
  fi
  rm -f "$pytest_log"
  TEST_STATUS="pass"

  # Optional contract test commands from spec.validation_plan.
  while IFS= read -r cmd; do
    [ -n "$cmd" ] || continue
    ctest_log="$(mktemp)"
    if ! (cd "$project_dir" && bash -lc "$cmd") >"$ctest_log" 2>&1; then
      first_line="$(first_meaningful_line "$ctest_log" | tr -d '\r')"
      if [ -z "$first_line" ]; then
        first_line="$(head -n 1 "$ctest_log" | tr -d '\r')"
      fi
      rm -f "$ctest_log"
      fail_gate "contract_test_failed" "contract test command failed: $cmd" "$first_line" "$project_dir" "$project_type"
    fi
    rm -f "$ctest_log"
  done < <(jq -r '.validation_plan.test_commands[]? // empty' "$SPEC_FILE" 2>/dev/null || true)

  write_summary "pass" "ok" "post-generation gate passed" "$project_dir" "$project_type" "" \
    "$JSON_STATUS" "$JS_STATUS" "$SH_STATUS" "$INSTALL_STATUS" "$REQUIRE_STATUS" "$TEST_STATUS"
  log_gate "pass" "ok" "$project_dir"
  exit 0
fi

# Minimum required files for web_app gate.
required_files=(
  "$project_dir/core/server.js"
  "$project_dir/core/public/app.js"
)
for rf in "${required_files[@]}"; do
  if [ ! -f "$rf" ]; then
    fail_gate "missing_required_file" "required file missing: $rf" "$rf" "$project_dir" "$project_type"
  fi
done

# Detect frequently observed malformed quote artifacts from generator output.
artifact_files=(
  "$project_dir/package.json"
  "$project_dir/core/package.json"
  "$project_dir/core/server.js"
  "$project_dir/core/public/app.js"
)
for af in "${artifact_files[@]}"; do
  [ -f "$af" ] || continue
  for pat in "\"'^" "\"''^" "\"'!"; do
    if grep -Fq "$pat" "$af"; then
      fail_gate "malformed_quote_artifact" "suspicious malformed quote artifact detected: $af" "$af" "$project_dir" "$project_type"
    fi
  done
done

# JSON syntax checks
for jf in "$project_dir/package.json" "$project_dir/core/package.json"; do
  [ -f "$jf" ] || continue
  if ! jq -e . "$jf" >/dev/null 2>&1; then
    JSON_STATUS="fail"
    fail_gate "json_syntax_failed" "invalid JSON: $jf" "$jf" "$project_dir" "$project_type"
  fi
done
JSON_STATUS="pass"

# JavaScript syntax checks
if ! command -v node >/dev/null 2>&1; then
  JS_STATUS="fail"
  fail_gate "node_missing" "node is required for web_app gate checks" "node command not found" "$project_dir" "$project_type"
fi
while IFS= read -r jsf; do
  [ -n "$jsf" ] || continue
  if ! node --check "$jsf" >/dev/null 2>&1; then
    JS_STATUS="fail"
    fail_gate "js_syntax_failed" "JavaScript syntax check failed: $jsf" "$jsf" "$project_dir" "$project_type"
  fi
done < <(find "$project_dir/core" -type f -name '*.js' -not -path '*/node_modules/*' 2>/dev/null | sort)
JS_STATUS="pass"

# Shell syntax checks for generated shell files (if any).
sh_files="$(find "$project_dir" -type f -name '*.sh' 2>/dev/null || true)"
if [ -n "$sh_files" ]; then
  while IFS= read -r shf; do
    [ -n "$shf" ] || continue
    if ! bash -n "$shf" >/dev/null 2>&1; then
      SH_STATUS="fail"
      fail_gate "shell_syntax_failed" "shell syntax check failed: $shf" "$shf" "$project_dir" "$project_type"
    fi
  done <<< "$sh_files"
  SH_STATUS="pass"
else
  SH_STATUS="skipped"
fi

# Resolve npm root (prefer project root package.json, fallback to core/package.json).
npm_root="$project_dir"
if [ ! -f "$npm_root/package.json" ] && [ -f "$project_dir/core/package.json" ]; then
  npm_root="$project_dir/core"
fi
if [ ! -f "$npm_root/package.json" ]; then
  INSTALL_STATUS="fail"
  fail_gate "package_json_missing" "package.json not found for web_app" "$project_dir" "$project_dir" "$project_type"
fi

if ! command -v npm >/dev/null 2>&1; then
  INSTALL_STATUS="fail"
  fail_gate "npm_missing" "npm is required for web_app gate checks" "npm command not found" "$project_dir" "$project_type"
fi

install_log="$(mktemp)"
if ! (cd "$npm_root" && npm install --no-audit --no-fund) >"$install_log" 2>&1; then
  INSTALL_STATUS="fail"
  first_line="$(first_meaningful_line "$install_log" | tr -d '\r')"
  if [ -z "$first_line" ]; then
    first_line="$(head -n 1 "$install_log" | tr -d '\r')"
  fi
  rm -f "$install_log"
  fail_gate "install_failed" "npm install failed at $npm_root" "$first_line" "$project_dir" "$project_type"
fi
if [ ! -d "$npm_root/node_modules" ]; then
  INSTALL_STATUS="fail"
  rm -f "$install_log"
  fail_gate "install_failed" "node_modules not found after npm install at $npm_root" "$npm_root/node_modules" "$project_dir" "$project_type"
fi
rm -f "$install_log"
INSTALL_STATUS="pass"

require_log="$(mktemp)"
if command -v jq >/dev/null 2>&1; then
  while IFS= read -r dep; do
    [ -n "$dep" ] || continue
    if ! (cd "$npm_root" && node -e "require('$dep')") >"$require_log" 2>&1; then
      REQUIRE_STATUS="fail"
      first_line="$(first_meaningful_line "$require_log" | tr -d '\r')"
      if [ -z "$first_line" ]; then
        first_line="$(head -n 1 "$require_log" | tr -d '\r')"
      fi
      rm -f "$require_log"
      fail_gate "require_check_failed" "dependency require() failed: $dep" "$first_line" "$project_dir" "$project_type"
    fi
  done < <(cd "$npm_root" && jq -r '.dependencies // {} | keys[]' package.json 2>/dev/null || true)
fi
rm -f "$require_log"
REQUIRE_STATUS="pass"

test_log="$(mktemp)"
if ! (cd "$npm_root" && npm test) >"$test_log" 2>&1; then
  TEST_STATUS="fail"
  first_line="$(first_meaningful_line "$test_log" | tr -d '\r')"
  if [ -z "$first_line" ]; then
    first_line="$(head -n 1 "$test_log" | tr -d '\r')"
  fi
  rm -f "$test_log"
  fail_gate "tests_failed" "npm test failed at $npm_root" "$first_line" "$project_dir" "$project_type"
fi
rm -f "$test_log"
TEST_STATUS="pass"

# Optional contract test commands from spec.validation_plan.
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  ctest_log="$(mktemp)"
  if ! (cd "$project_dir" && bash -lc "$cmd") >"$ctest_log" 2>&1; then
    first_line="$(first_meaningful_line "$ctest_log" | tr -d '\r')"
    if [ -z "$first_line" ]; then
      first_line="$(head -n 1 "$ctest_log" | tr -d '\r')"
    fi
    rm -f "$ctest_log"
    fail_gate "contract_test_failed" "contract test command failed: $cmd" "$first_line" "$project_dir" "$project_type"
  fi
  rm -f "$ctest_log"
done < <(jq -r '.validation_plan.test_commands[]? // empty' "$SPEC_FILE" 2>/dev/null || true)

write_summary "pass" "ok" "post-generation gate passed" "$project_dir" "$project_type" "" \
  "$JSON_STATUS" "$JS_STATUS" "$SH_STATUS" "$INSTALL_STATUS" "$REQUIRE_STATUS" "$TEST_STATUS"
log_gate "pass" "ok" "$project_dir"
exit 0
