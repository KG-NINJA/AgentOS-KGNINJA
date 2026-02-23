#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
RUNTIME_DIR="runtime"
TMP_DIR="$(mktemp -d)"

cleanup() {
  set +e
  rm -f "$QUEUE_DIR"/*.md
  if [ -d "$TMP_DIR/queue" ]; then
    cp -a "$TMP_DIR/queue/." "$QUEUE_DIR/" 2>/dev/null || true
  fi

  if [ -f "$TMP_DIR/interpret.json" ]; then
    cp -a "$TMP_DIR/interpret.json" "$RUNTIME_DIR/interpret.json"
  else
    rm -f "$RUNTIME_DIR/interpret.json"
  fi

  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$QUEUE_DIR" "$RUNTIME_DIR" "$TMP_DIR/queue"
cp -a "$QUEUE_DIR/." "$TMP_DIR/queue/" 2>/dev/null || true
if [ -f "$RUNTIME_DIR/interpret.json" ]; then
  cp -a "$RUNTIME_DIR/interpret.json" "$TMP_DIR/interpret.json"
fi

rm -f "$QUEUE_DIR"/*.md
cat > "$QUEUE_DIR/test-interpret.md" <<'EOF'
Build a web app to generate music from voice input with a web interface.
EOF

# Force fallback path regardless of local codex availability.
PATH="/usr/bin:/bin" bash ./factory/parser/interpret.sh

jq -e '.project_type == "web_app"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ai_task == "unknown"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.input_type == "voice_input"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ui_type == "web_interface"' "$RUNTIME_DIR/interpret.json" >/dev/null

# Japanese fallback coverage.
cat > "$QUEUE_DIR/test-interpret.md" <<'EOF'
音声入力から音楽を生成するウェブアプリを作る
EOF

PATH="/usr/bin:/bin" bash ./factory/parser/interpret.sh

jq -e '.project_type == "web_app"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ai_task == "unknown"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.input_type == "voice_input"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ui_type == "web_interface"' "$RUNTIME_DIR/interpret.json" >/dev/null

# Desktop monitor fallback coverage.
cat > "$QUEUE_DIR/test-interpret.md" <<'EOF'
PC画面の任意範囲を監視し、差分が閾値を超えたらMarkdownへ記録する。
技術スタック: Python 3.11 / PySide6 / mss / opencv-python / scikit-image
EOF

PATH="/usr/bin:/bin" bash ./factory/parser/interpret.sh

jq -e '.project_type == "desktop_app"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ai_task == "screen_change_monitoring"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.input_type == "screen_capture"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ui_type == "desktop_gui"' "$RUNTIME_DIR/interpret.json" >/dev/null

# Structured markdown fields should win even with noisy text.
cat > "$QUEUE_DIR/test-interpret.md" <<'EOF'
# IDEA
project\_type: cli_tool
ai\_task: classification
input\_type: text_input
ui\_type: cli
also mentions web voice generate, but structured keys are authoritative.
EOF

PATH="/usr/bin:/bin" bash ./factory/parser/interpret.sh

jq -e '.project_type == "cli_tool"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ai_task == "classification"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.input_type == "text_input"' "$RUNTIME_DIR/interpret.json" >/dev/null
jq -e '.ui_type == "cli"' "$RUNTIME_DIR/interpret.json" >/dev/null

echo "interpret tests: OK"
