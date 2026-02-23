#!/usr/bin/env bash
# Regression tests for fallback web_app scaffold generation in create_project.sh.
# Ensures required files, package.json shape, and offline npm/node checks all pass.
set -euo pipefail

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

pass() {
  echo "[PASS] $*"
}

assert_file_exists() {
  local path="$1"
  [ -f "$path" ] || fail "Required file missing: $path"
}

assert_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
}

assert_json_valid() {
  local file="$1"
  python3 - "$file" <<'PY' || exit 1
import json
import sys
path = sys.argv[1]
try:
    with open(path, 'r', encoding='utf-8') as f:
        json.load(f)
except Exception as e:
    print(f"Invalid JSON in {path}: {e}", file=sys.stderr)
    raise SystemExit(1)
PY
}

assert_package_policy() {
  local file="$1"
  python3 - "$file" <<'PY' || exit 1
import json
import sys
path = sys.argv[1]
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

deps = data.get('dependencies', {})
dev_deps = data.get('devDependencies', {})
if deps not in ({}, None):
    print(f"dependencies must be empty, got: {deps}", file=sys.stderr)
    raise SystemExit(1)
if dev_deps not in ({}, None):
    print(f"devDependencies must be empty, got: {dev_deps}", file=sys.stderr)
    raise SystemExit(1)

scripts = data.get('scripts', {})
if not isinstance(scripts, dict) or 'test' not in scripts:
    print("scripts.test is required in core/package.json", file=sys.stderr)
    raise SystemExit(1)
PY
}

ROOT_TMP="$(mktemp -d /tmp/factory-fallback-test.XXXXXX)"
cleanup() {
  rm -rf "$ROOT_TMP"
}
trap cleanup EXIT

assert_command python3
assert_command node
assert_command npm

mkdir -p "$ROOT_TMP/factory/generator"
mkdir -p "$ROOT_TMP/factory/memory"
mkdir -p "$ROOT_TMP/queue"
mkdir -p "$ROOT_TMP/factory/templates"
mkdir -p "$ROOT_TMP/workspace"
mkdir -p "$ROOT_TMP/runtime"

cp /home/user/kg-autonomous/factory/generator/create_project.sh "$ROOT_TMP/factory/generator/create_project.sh"
chmod +x "$ROOT_TMP/factory/generator/create_project.sh"
cp -a /home/user/kg-autonomous/factory/templates/base-project "$ROOT_TMP/factory/templates/base-project"

# Stub memory recorder so create_project.sh can run in isolation.
cat > "$ROOT_TMP/factory/memory/record.sh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
exit 0
STUB
chmod +x "$ROOT_TMP/factory/memory/record.sh"

# Queue input and runtime spec for web_app fallback generation.
cat > "$ROOT_TMP/queue/idea.md" <<'IDEA'
# test idea
build fallback web app
IDEA

cat > "$ROOT_TMP/runtime/spec.json" <<'SPEC'
{
  "project_type": "web_app"
}
SPEC

# Run fallback scaffold generator under isolated temp root.
(
  cd "$ROOT_TMP"
  ./factory/generator/create_project.sh
) || fail "create_project.sh fallback generation failed"

PROJECT_DIR="$ROOT_TMP/workspace/project-001"
CORE_DIR="$PROJECT_DIR/core"

# 1) Required files must exist.
assert_file_exists "$CORE_DIR/server.js"
assert_file_exists "$CORE_DIR/public/app.js"
assert_file_exists "$CORE_DIR/package.json"
pass "Required fallback scaffold files exist"

# 2) package.json validity and policy checks.
assert_json_valid "$CORE_DIR/package.json"
assert_package_policy "$CORE_DIR/package.json"
pass "core/package.json is valid and policy-compliant"

# 3) JavaScript syntax checks.
node --check "$CORE_DIR/server.js" >/dev/null 2>&1 || fail "node --check failed for core/server.js"
node --check "$CORE_DIR/public/app.js" >/dev/null 2>&1 || fail "node --check failed for core/public/app.js"
pass "node --check passed for server.js and app.js"

# 4) npm install must succeed offline with no dependencies.
(
  cd "$CORE_DIR"
  npm install --no-audit --no-fund >/dev/null 2>&1
) || fail "npm install failed in fallback core/ scaffold"
pass "npm install succeeded"

# 5) npm test must exit 0.
(
  cd "$CORE_DIR"
  npm test >/dev/null 2>&1
) || fail "npm test failed in fallback core/ scaffold"
pass "npm test exited 0"

echo "All fallback scaffold regression tests passed."
