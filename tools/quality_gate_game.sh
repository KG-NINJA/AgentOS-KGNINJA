#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

SPEC_FILE="runtime/spec.json"
RUNTIME_DIR="runtime"
QUALITY_META="$RUNTIME_DIR/.quality_gate_meta"
mkdir -p "$RUNTIME_DIR"

if [ ! -f "$SPEC_FILE" ]; then
  exit 0
fi

record_quality_gate_result() {
  local status="$1"
  local score="${2:-NA}"
  local threshold="${3:-NA}"
  local project="${4:-none}"
  local reason="${5:-}"
  local ts
  ts="$(date -Is)"
  local line="$ts QUALITY_GATE status=$status score=$score threshold=$threshold project=$project"
  if [ -n "$reason" ]; then
    local sanitized
    sanitized="$(printf '%s' "$reason" | tr '\n' ' ')"
    line="$line reason=\"$sanitized\""
  fi
  printf '%s\n' "$line" >> "$RUNTIME_DIR/index.log"
  if [ "$project" != "none" ]; then
    printf '%s\n' "$project" > "$RUNTIME_DIR/.last_generated_project"
  fi
  printf '%s\n' "$line" > "$QUALITY_META"
}

fail_gate() {
  local reason="$1"
  local score_value="${score:-NA}"
  local threshold_value="${threshold:-NA}"
  local project_value="${latest_project:-none}"
  echo "QUALITY_GATE: $reason" >&2
  record_quality_gate_result "fail" "$score_value" "$threshold_value" "$project_value" "$reason"
  exit 1
}

if ! command -v jq >/dev/null 2>&1; then
  fail_gate "jq not found"
fi

has_pattern() {
  local pattern="$1"
  local file="$2"
  if command -v rg >/dev/null 2>&1; then
    rg -q "$pattern" "$file"
  else
    grep -Eq "$pattern" "$file"
  fi
}

mode="$(jq -r '.quality_policy.mode // "balanced"' "$SPEC_FILE" 2>/dev/null || echo "balanced")"
focus="$(jq -r '.quality_policy.focus // "balanced"' "$SPEC_FILE" 2>/dev/null || echo "balanced")"
simulation_level="$(jq -r '.quality_policy.simulation_level // "low"' "$SPEC_FILE" 2>/dev/null || echo "low")"
project_type="$(jq -r '.project_type // "unknown"' "$SPEC_FILE" 2>/dev/null || echo "unknown")"

shopt -s nullglob
projects=(workspace/project-*)
shopt -u nullglob
if [ "${#projects[@]}" -eq 0 ]; then
  fail_gate "no generated project found"
fi

latest_project="$(ls -dt workspace/project-* 2>/dev/null | head -n 1)"
if [ -z "$latest_project" ] || [ ! -d "$latest_project" ]; then
  fail_gate "latest project not found"
fi

# Contract file checks (Open MD pipeline).
while IFS= read -r cfile; do
  [ -n "$cfile" ] || continue
  if [ ! -f "$latest_project/$cfile" ]; then
    fail_gate "contract file missing: $cfile"
  fi
done < <(jq -r '.contracts.files[]? // empty' "$SPEC_FILE" 2>/dev/null || true)

score=0
threshold=3
if [ "$mode" = "quality_first" ]; then
  threshold=4
fi
if [ "$focus" = "visual_first" ]; then
  threshold=$((threshold + 1))
fi
if [ "$simulation_level" = "medium" ]; then
  threshold=$((threshold + 1))
elif [ "$simulation_level" = "high" ]; then
  threshold=$((threshold + 2))
fi
if [ "$threshold" -gt 6 ]; then
  threshold=6
fi

if [ "$project_type" = "desktop_app" ]; then
  required=(
    "$latest_project/core/app.py"
    "$latest_project/core/ui/main_window.py"
    "$latest_project/core/services/diff_detector.py"
    "$latest_project/core/services/markdown_logger.py"
    "$latest_project/core/services/notifier.py"
    "$latest_project/requirements.txt"
    "$latest_project/docs/ARCHITECTURE.md"
    "$latest_project/tests/test_diff_detector.py"
    "$latest_project/README.md"
  )

  missing=0
  missing_detail=""
  for file in "${required[@]}"; do
    if [ ! -f "$file" ]; then
      echo "QUALITY_GATE: missing $file" >&2
      missing=1
      missing_detail="$missing_detail$(basename "$file") "
    fi
  done
  if [ "$missing" -ne 0 ]; then
    fail_gate "required files missing: ${missing_detail:-unknown}"
  fi

  if [ ! -f "$latest_project/logs/.gitkeep" ] && [ ! -d "$latest_project/logs" ]; then
    fail_gate "missing logs directory"
  fi

  if ! has_pattern "PySide6" "$latest_project/requirements.txt"; then
    fail_gate "requirements.txt missing PySide6"
  fi
  if ! has_pattern "mss" "$latest_project/requirements.txt"; then
    fail_gate "requirements.txt missing mss"
  fi
  if ! has_pattern "opencv-python|opencv" "$latest_project/requirements.txt"; then
    fail_gate "requirements.txt missing opencv-python"
  fi
  if ! has_pattern "scikit-image" "$latest_project/requirements.txt"; then
    fail_gate "requirements.txt missing scikit-image"
  fi

  if command -v python3 >/dev/null 2>&1; then
    if ! python3 -m compileall "$latest_project/core" "$latest_project/tests" >/dev/null 2>&1; then
      fail_gate "python compile check failed"
    fi
  fi

  app_size="$(wc -c < "$latest_project/core/app.py" | tr -d ' ')"
  detector_size="$(wc -c < "$latest_project/core/services/diff_detector.py" | tr -d ' ')"
  logger_size="$(wc -c < "$latest_project/core/services/markdown_logger.py" | tr -d ' ')"

  if [ "$app_size" -ge 400 ]; then score=$((score + 1)); fi
  if [ "$detector_size" -ge 500 ]; then score=$((score + 1)); fi
  if [ "$logger_size" -ge 350 ]; then score=$((score + 1)); fi

  if has_pattern "ssim|change_score|threshold|diff" "$latest_project/core/services/diff_detector.py"; then
    score=$((score + 1))
  fi
  if has_pattern "markdown|logs|YYYY-MM-DD|append" "$latest_project/core/services/markdown_logger.py"; then
    score=$((score + 1))
  fi
  if has_pattern "pytest|assert" "$latest_project/tests/test_diff_detector.py"; then
    score=$((score + 1))
  fi
else
  required=(
    "$latest_project/core/package.json"
    "$latest_project/core/server.js"
    "$latest_project/core/public/index.html"
    "$latest_project/core/public/app.js"
    "$latest_project/core/public/style.css"
    "$latest_project/docs/ARCHITECTURE.md"
    "$latest_project/tests/api.test.js"
    "$latest_project/tests/ui.test.js"
    "$latest_project/README.md"
  )

  missing=0
  missing_detail=""
  for file in "${required[@]}"; do
    if [ ! -f "$file" ]; then
      echo "QUALITY_GATE: missing $file" >&2
      missing=1
      missing_detail="$missing_detail$(basename "$file") "
    fi
  done
  if [ "$missing" -ne 0 ]; then
    fail_gate "required files missing: ${missing_detail:-unknown}"
  fi

  if ! jq -e . "$latest_project/core/package.json" >/dev/null 2>&1; then
    fail_gate "invalid JSON in $latest_project/core/package.json"
  fi

  if command -v node >/dev/null 2>&1; then
    js_files=(
      "$latest_project/core/server.js"
      "$latest_project/core/public/app.js"
    )
    for file in "${js_files[@]}"; do
      if ! node --check "$file" >/dev/null 2>&1; then
        fail_gate "JavaScript syntax check failed: $file"
      fi
    done
  fi

  css_size="$(wc -c < "$latest_project/core/public/style.css" | tr -d ' ')"
  js_size="$(wc -c < "$latest_project/core/public/app.js" | tr -d ' ')"
  api_size="$(wc -c < "$latest_project/core/server.js" | tr -d ' ')"

  if [ "$css_size" -ge 400 ]; then score=$((score + 1)); fi
  if [ "$js_size" -ge 1200 ]; then score=$((score + 1)); fi
  if [ "$api_size" -ge 800 ]; then score=$((score + 1)); fi

  if has_pattern "requestAnimationFrame|canvas|getContext|sprite|animation" "$latest_project/core/public/app.js"; then
    score=$((score + 1))
  fi

  if has_pattern "collision|physics|velocity|acceleration|drag|resistance|spawn" "$latest_project/core/public/app.js"; then
    score=$((score + 1))
  fi

  if has_pattern "describe\\(|test\\(" "$latest_project/tests/api.test.js" && has_pattern "describe\\(|test\\(" "$latest_project/tests/ui.test.js"; then
    score=$((score + 1))
  fi
fi

if [ "$score" -lt "$threshold" ]; then
  fail_gate "score below threshold"
fi

# Contract behavior checks (best-effort keyword presence).
while IFS= read -r behavior; do
  [ -n "$behavior" ] || continue
  case "$behavior" in
    *diff*|*threshold*)
      if ! rg -q "diff|threshold|change_score|ssim" "$latest_project" 2>/dev/null; then
        fail_gate "contract behavior missing: $behavior"
      fi
      ;;
    *markdown*)
      if ! rg -q "markdown|\\.md|logs" "$latest_project" 2>/dev/null; then
        fail_gate "contract behavior missing: $behavior"
      fi
      ;;
  esac
done < <(jq -r '.contracts.behaviors[]? // empty' "$SPEC_FILE" 2>/dev/null || true)

echo "QUALITY_GATE: passed score=$score threshold=$threshold project=$latest_project"
record_quality_gate_result "pass" "$score" "$threshold" "$latest_project" ""
exit 0
