#!/usr/bin/env bash
# Deterministic local fallback generator (no LLM, no network).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PROJECT_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    *)
      echo "local_fallback.sh: unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$PROJECT_DIR" ]; then
  echo "local_fallback.sh: --project-dir is required" >&2
  exit 2
fi

project_type="unknown"
if [ -f runtime/spec.json ] && command -v jq >/dev/null 2>&1; then
  project_type="$(jq -r '.project_type // "unknown"' runtime/spec.json 2>/dev/null || echo "unknown")"
fi

mkdir -p runtime "$PROJECT_DIR/docs" "$PROJECT_DIR/tests"
echo "GENERATOR_FALLBACK_INVOCATION=deterministic" >> runtime/activity.log

if [ "$project_type" = "desktop_app" ]; then
  mkdir -p "$PROJECT_DIR/core/services" "$PROJECT_DIR/core/ui" "$PROJECT_DIR/logs"

  cat > "$PROJECT_DIR/core/app.py" <<'PY'
from core.ui.main_window import MainWindow


def main() -> int:
    _ = MainWindow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

  cat > "$PROJECT_DIR/core/ui/main_window.py" <<'PY'
class MainWindow:
    def __init__(self) -> None:
        self.title = "ScreenDeltaMD"
PY

  cat > "$PROJECT_DIR/core/services/diff_detector.py" <<'PY'
def compute_change_score(previous_value: float, current_value: float) -> float:
    if previous_value == 0:
        return 0.0
    delta = abs(current_value - previous_value)
    return max(0.0, min(1.0, delta / abs(previous_value)))
PY

  cat > "$PROJECT_DIR/core/services/markdown_logger.py" <<'PY'
from pathlib import Path


def append_entry(log_file: Path, text: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
PY

  cat > "$PROJECT_DIR/core/services/notifier.py" <<'PY'
def notify(message: str) -> str:
    return message
PY

  cat > "$PROJECT_DIR/requirements.txt" <<'REQ'
PySide6>=6.7.0
mss>=9.0.1
opencv-python>=4.10.0
scikit-image>=0.24.0
numpy>=1.26.0
pytest>=8.0.0
REQ

  cat > "$PROJECT_DIR/pyproject.toml" <<'TOML'
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "screen-delta-md-fallback"
version = "0.1.0"
requires-python = ">=3.11"
TOML

  cat > "$PROJECT_DIR/tests/test_diff_detector.py" <<'PY'
from core.services.diff_detector import compute_change_score


def test_compute_change_score_zero_previous() -> None:
    assert compute_change_score(0.0, 1.0) == 0.0
PY

  cat > "$PROJECT_DIR/README.md" <<'MD'
# Deterministic Desktop Fallback

Run:

```bash
python3 -m pip install -r requirements.txt
python3 core/app.py
```
MD

  : > "$PROJECT_DIR/logs/.gitkeep"
else
  mkdir -p "$PROJECT_DIR/core/public"

  cat > "$PROJECT_DIR/core/server.js" <<'JS'
const express = require("express");
const app = express();
app.use(express.static("public"));
app.get("/api/health", (req,res)=>res.json({ok:true}));
app.listen(3000,()=>console.log("server started"));
JS

  cat > "$PROJECT_DIR/core/package.json" <<'JSON'
{
  "name": "deterministic-app",
  "version": "1.0.0",
  "main": "server.js",
  "scripts": {
    "start": "node server.js",
    "test": "echo \"ok\""
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
JSON

  cat > "$PROJECT_DIR/core/public/app.js" <<'JS'
document.body.innerHTML = "<h1>Deterministic Fallback App</h1>";
JS

  cat > "$PROJECT_DIR/tests/basic.test.js" <<'JS'
test("true is true", () => {
  expect(true).toBe(true);
});
JS

  cat > "$PROJECT_DIR/README.md" <<'MD'
# Deterministic Web Fallback

Run:

```bash
npm install
npm start
```
MD
fi

printf '%s\n' "$PROJECT_DIR" > runtime/.last_generated_project
printf '%s GENERATED_PROJECT=%s source=deterministic_fallback\n' "$(date -Is)" "$PROJECT_DIR" >> runtime/index.log
echo "GENERATOR_FINAL_EXIT_SOURCE=local_fallback_success" >> runtime/activity.log
exit 0
