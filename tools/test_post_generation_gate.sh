#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

make_stub_tools() {
  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/jq" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-r" ] && [[ "$2" == .project_type* ]]; then
  echo "${PROJECT_TYPE:-web_app}"
  exit 0
fi
if [ "$1" = "-e" ] && [ "$2" = "." ]; then
  python3 - <<'PY' "$3"
import json, sys
json.load(open(sys.argv[1], encoding="utf-8"))
PY
  exit 0
fi
if [ "$1" = "-r" ] && [[ "$2" == .dependencies* ]]; then
  python3 - <<'PY' "$PWD/package.json"
import json, sys
deps = json.load(open(sys.argv[1], encoding="utf-8")).get("dependencies", {})
for k in deps.keys():
    print(k)
PY
  exit 0
fi
exit 0
EOF
  chmod +x "$tmp/bin/jq"

  cat > "$tmp/bin/node" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "--check" ]; then
  python3 - <<'PY' "$2"
import sys
txt = open(sys.argv[1], encoding="utf-8").read()
if "BROKEN_JS" in txt:
    raise SystemExit(1)
PY
  exit $?
fi
if [ "$1" = "-e" ]; then
  code="$2"
  if [[ "$code" == *"require('missing-dep')"* ]]; then
    exit 1
  fi
  exit 0
fi
exit 0
EOF
  chmod +x "$tmp/bin/node"

  cat > "$tmp/bin/npm" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "install" ]; then
  if [ "${FORCE_INSTALL_FAIL:-0}" = "1" ]; then
    exit 1
  fi
  mkdir -p node_modules
  exit 0
fi
if [ "$1" = "test" ]; then
  if [ "${FORCE_TEST_FAIL:-0}" = "1" ]; then
    echo "ERROR: test execution failed"
    exit 1
  fi
  exit 0
fi
exit 0
EOF
  chmod +x "$tmp/bin/npm"

  cat > "$tmp/bin/python3" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then
  if [ "${FORCE_PYTEST_FAIL:-0}" = "1" ]; then
    echo "pytest failed"
    exit 1
  fi
  exit 0
fi
exec /usr/bin/python3 "$@"
EOF
  chmod +x "$tmp/bin/python3"
}

prepare_project() {
  rm -rf "$tmp/workspace" "$tmp/runtime"
  mkdir -p "$tmp/workspace/project-001/core/routes" "$tmp/workspace/project-001/core/services" "$tmp/workspace/project-001/core/public" "$tmp/workspace/project-001/tests" "$tmp/runtime"
  cat > "$tmp/runtime/spec.json" <<'EOF'
{"project_type":"web_app"}
EOF
  echo "workspace/project-001" > "$tmp/runtime/.last_generated_project"
  cat > "$tmp/workspace/project-001/package.json" <<'EOF'
{"name":"x","dependencies":{"ok-dep":"1.0.0"}}
EOF
  cat > "$tmp/workspace/project-001/core/package.json" <<'EOF'
{"name":"x-core"}
EOF
  cat > "$tmp/workspace/project-001/core/server.js" <<'EOF'
console.log("ok");
EOF
  cat > "$tmp/workspace/project-001/core/routes/a.js" <<'EOF'
console.log("ok");
EOF
  cat > "$tmp/workspace/project-001/core/services/a.js" <<'EOF'
console.log("ok");
EOF
  cat > "$tmp/workspace/project-001/core/public/app.js" <<'EOF'
console.log("ok");
EOF
}

prepare_desktop_project() {
  rm -rf "$tmp/workspace" "$tmp/runtime"
  mkdir -p "$tmp/workspace/project-001/core/services" "$tmp/workspace/project-001/core/ui" "$tmp/workspace/project-001/tests" "$tmp/workspace/project-001/docs" "$tmp/workspace/project-001/logs" "$tmp/runtime"
  cat > "$tmp/runtime/spec.json" <<'EOF'
{"project_type":"desktop_app"}
EOF
  echo "workspace/project-001" > "$tmp/runtime/.last_generated_project"
  cat > "$tmp/workspace/project-001/core/app.py" <<'EOF'
from core.ui.main_window import MainWindow

def main():
    _ = MainWindow()
    return 0
EOF
  cat > "$tmp/workspace/project-001/core/ui/main_window.py" <<'EOF'
class MainWindow:
    pass
EOF
  cat > "$tmp/workspace/project-001/core/services/diff_detector.py" <<'EOF'
def score(a, b):
    return abs(a - b)
EOF
  cat > "$tmp/workspace/project-001/core/services/markdown_logger.py" <<'EOF'
def append_line(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")
EOF
  cat > "$tmp/workspace/project-001/core/services/notifier.py" <<'EOF'
def notify(text):
    return text
EOF
  cat > "$tmp/workspace/project-001/tests/test_diff_detector.py" <<'EOF'
def test_true():
    assert True
EOF
  cat > "$tmp/workspace/project-001/docs/ARCHITECTURE.md" <<'EOF'
# Arch
EOF
  cat > "$tmp/workspace/project-001/requirements.txt" <<'EOF'
PySide6>=6.7.0
mss>=9.0.1
opencv-python>=4.10.0
scikit-image>=0.24.0
pytest>=8.0.0
EOF
  : > "$tmp/workspace/project-001/logs/.gitkeep"
}

run_gate_expect() {
  local expect_code="$1"
  shift
  set +e
  (
    cd "$tmp"
    PATH="$tmp/bin:$PATH" FACTORY_ROOT="$tmp" PROJECT_TYPE="${PROJECT_TYPE:-web_app}" "$ROOT/tools/post_generation_gate.sh" "$@"
  )
  local code=$?
  set -e
  if [ "$code" -ne "$expect_code" ]; then
    echo "expected exit=$expect_code got=$code"
    exit 1
  fi
}

make_stub_tools
prepare_project

# pass case
run_gate_expect 0
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["status"] == "pass"
PY

# js syntax fail
prepare_project
echo "BROKEN_JS" > "$tmp/workspace/project-001/core/server.js"
run_gate_expect 1
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["status"] == "fail"
assert data["reason_code"] == "js_syntax_failed"
PY

# install fail
prepare_project
(
  cd "$tmp"
  PATH="$tmp/bin:$PATH" FACTORY_ROOT="$tmp" FORCE_INSTALL_FAIL=1 "$ROOT/tools/post_generation_gate.sh" >/dev/null 2>&1 || true
)
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["reason_code"] == "install_failed"
PY

# require fail
prepare_project
cat > "$tmp/workspace/project-001/package.json" <<'EOF'
{"name":"x","dependencies":{"missing-dep":"1.0.0"}}
EOF
(
  cd "$tmp"
  PATH="$tmp/bin:$PATH" FACTORY_ROOT="$tmp" "$ROOT/tools/post_generation_gate.sh" >/dev/null 2>&1 || true
)
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["reason_code"] == "require_check_failed"
PY

# tests fail
prepare_project
(
  cd "$tmp"
  PATH="$tmp/bin:$PATH" FACTORY_ROOT="$tmp" FORCE_TEST_FAIL=1 "$ROOT/tools/post_generation_gate.sh" >/dev/null 2>&1 || true
)
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["reason_code"] == "tests_failed"
assert data["first_error_line"], "first_error_line should not be empty"
PY

# desktop pass case
prepare_desktop_project
PROJECT_TYPE=desktop_app run_gate_expect 0
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["status"] == "pass"
PY

# desktop missing dependency
prepare_desktop_project
cat > "$tmp/workspace/project-001/requirements.txt" <<'EOF'
PySide6>=6.7.0
pytest>=8.0.0
EOF
(
  cd "$tmp"
  PATH="$tmp/bin:$PATH" FACTORY_ROOT="$tmp" PROJECT_TYPE=desktop_app "$ROOT/tools/post_generation_gate.sh" >/dev/null 2>&1 || true
)
python3 - <<'PY' "$tmp/runtime/failure_summary.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["reason_code"] == "missing_dependency_decl"
PY

echo "test_post_generation_gate: OK"
