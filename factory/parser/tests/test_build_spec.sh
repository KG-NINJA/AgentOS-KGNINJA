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
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
  if [ -d "$TMP_DIR/queue" ]; then
    cp -a "$TMP_DIR/queue/." "$QUEUE_DIR/" 2>/dev/null || true
  fi

  for file in interpret.json entities.json spec.json index.log intent_ir.json .queue_target; do
    if [ -f "$TMP_DIR/$file" ]; then
      cp -a "$TMP_DIR/$file" "$RUNTIME_DIR/$file"
    else
      rm -f "$RUNTIME_DIR/$file"
    fi
  done

  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$QUEUE_DIR" "$RUNTIME_DIR" "$TMP_DIR/queue"

cp -a "$QUEUE_DIR/." "$TMP_DIR/queue/" 2>/dev/null || true
for file in interpret.json entities.json spec.json index.log intent_ir.json .queue_target; do
  if [ -f "$RUNTIME_DIR/$file" ]; then
    cp -a "$RUNTIME_DIR/$file" "$TMP_DIR/$file"
  fi
done

assert_jq() {
  local expr="$1"
  if ! jq -e "$expr" "$RUNTIME_DIR/spec.json" >/dev/null; then
    echo "assert failed: $expr" >&2
    cat "$RUNTIME_DIR/spec.json" >&2
    exit 1
  fi
}

# Case 1: keyword inference triggers full_app and api defaults are present.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-1.md" <<'EOF'
Build web voice generate application with frontend and api.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "web_app",
  "ai_task": "music_generation",
  "input_type": "voice_input",
  "ui_type": "web_interface"
}
EOF

cat > "$RUNTIME_DIR/entities.json" <<'EOF'
{
  "noun": [],
  "verb": [],
  "modifier": []
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.intent.app_level == "full_app"'
assert_jq '.intent.frontend_required == true'
assert_jq '.intent.api_required == true'
assert_jq '.api_contract.endpoint == "/api/generate"'
assert_jq '.api_contract.method == "POST"'

# Case 2: negative cues suppress forced full_app and preserve interpret intent.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-2.md" <<'EOF'
CLI only, no web, no api, backend only.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "cli_tool",
  "ai_task": "summarization",
  "input_type": "text_input",
  "ui_type": "cli",
  "intent": {
    "app_level": "module",
    "api_required": false,
    "frontend_required": false
  },
  "runtime_rules": {
    "strict_mode": true
  }
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.intent.app_level == "module"'
assert_jq '.intent.frontend_required == false'
assert_jq '.intent.api_required == false'
assert_jq '.runtime_rules.strict_mode == true'

# Case 3: unknown core fields are backfilled from queue signals.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-3.md" <<'EOF'
Build web voice generate music prototype with frontend.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "unknown",
  "ai_task": "unknown",
  "input_type": "unknown",
  "ui_type": "unknown"
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.project_type == "web_app"'
assert_jq '.ai_task == "unknown"'
assert_jq '.input_type == "voice_input"'
assert_jq '.ui_type == "web_interface"'

# Case 4: api_contract is normalized and invalid method falls back to POST.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-4.md" <<'EOF'
text processing module
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "module",
  "ai_task": "classification",
  "input_type": "text_input",
  "ui_type": "cli",
  "api_contract": {
    "endpoint": "api/custom",
    "method": "postx"
  }
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.api_contract.endpoint == "/api/custom"'
assert_jq '.api_contract.method == "POST"'

# Case 5: explicit negative intent blocks forced full_app inference.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-5.md" <<'EOF'
web voice generate app, but no api and no frontend.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "unknown",
  "ai_task": "unknown",
  "input_type": "unknown",
  "ui_type": "unknown"
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.intent.app_level != "full_app"'

# Case 6: runtime_rules keeps known valid keys and x_ extensions only.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-6.md" <<'EOF'
module parser
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "module",
  "ai_task": "classification",
  "input_type": "text_input",
  "ui_type": "cli",
  "runtime_rules": {
    "stability_priority": "low_ambiguity",
    "deterministic": true,
    "timeout_ms": 5000,
    "retry_policy": {
      "max_retries": 3,
      "backoff_ms": 250
    },
    "freeform": "drop_me",
    "x_team_hint": "keep_me",
    "x-bad": "drop_me_too",
    "x_oversized_timeout": 999999
  }
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.runtime_rules.stability_priority == "low_ambiguity"'
assert_jq '.runtime_rules.deterministic == true'
assert_jq '.runtime_rules.timeout_ms == 5000'
assert_jq '.runtime_rules.retry_policy.max_retries == 3'
assert_jq '.runtime_rules.retry_policy.backoff_ms == 250'
assert_jq '.runtime_rules.x_team_hint == "keep_me"'
assert_jq '(.runtime_rules | has("freeform")) == false'
assert_jq '(.runtime_rules | has("x-bad")) == false'
assert_jq '.runtime_rules.x_oversized_timeout == 999999'

# Case 7: invalid runtime_rules known fields are dropped by sanitizer.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-7.md" <<'EOF'
module parser runtime rules
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "module",
  "ai_task": "classification",
  "input_type": "text_input",
  "ui_type": "cli",
  "runtime_rules": {
    "timeout_ms": 999999,
    "retry_policy": {
      "max_retries": 99,
      "backoff_ms": -1
    },
    "x_safe": {
      "note": "kept"
    }
  }
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '(.runtime_rules | has("timeout_ms")) == false'
assert_jq '(.runtime_rules | has("retry_policy")) == false'
assert_jq '.runtime_rules.x_safe.note == "kept"'

# Case 8: quality_profile high_visual_game expands to strict quality_policy defaults.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-8.md" <<'EOF'
quality_profile: high_visual_game
Build a polished fishing game.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "web_app",
  "ai_task": "fishing_game_simulation",
  "input_type": "pointer_input",
  "ui_type": "web_interface"
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.quality_policy.mode == "quality_first"'
assert_jq '.quality_policy.focus == "visual_first"'
assert_jq '.quality_policy.simulation_level == "medium"'
assert_jq '.quality_policy.publish_gate == "strict"'
assert_jq '.quality_policy.auto_retry_on_fail == true'
assert_jq '.quality_policy.max_retry_count == 1'

# Case 9: interpret quality overrides apply over profile defaults.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-9.md" <<'EOF'
quality_profile: high_visual_game
Override quality behavior.
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "web_app",
  "ai_task": "fishing_game_simulation",
  "input_type": "pointer_input",
  "ui_type": "web_interface",
  "quality_mode": "speed_first",
  "quality_focus": "balanced",
  "simulation_level": "high",
  "publish_gate": "relaxed",
  "auto_retry_on_fail": false,
  "max_retry_count": 3
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.quality_policy.mode == "speed_first"'
assert_jq '.quality_policy.focus == "balanced"'
assert_jq '.quality_policy.simulation_level == "high"'
assert_jq '.quality_policy.publish_gate == "relaxed"'
assert_jq '.quality_policy.auto_retry_on_fail == false'
assert_jq '.quality_policy.max_retry_count == 3'

# Case 10: desktop monitoring spec keeps desktop intent and extracts requirements.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-10.md" <<'EOF'
\# ScreenDeltaMD
\## 1. ユースケース（Problem Framing）
\- 作業ログを残したい開発者
\## 2. MVPでやること（必須）
\- 監視領域の指定
\- 変化があったときだけMarkdown生成
\- `logs/YYYY-MM-DD.md` に追記
\- 画像は `logs/assets/YYYY-MM-DD/` に保存
\## 3. “目立つ変化”の定義（実装指針）
\- SSIM
\- change\_score = 1 - SSIM(gray(prev), gray(curr))
\- 画像を縮小（例：幅640）
\## 5. 技術スタック（提案）
\- 言語：Python 3.11+
\- GUI：PySide6（Qt）
\- 画面キャプチャ：mss
\- 画像処理：opencv-python
\- SSIM：scikit-image
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "desktop_app",
  "ai_task": "screen_change_monitoring",
  "input_type": "screen_capture",
  "ui_type": "desktop_gui"
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.project_type == "desktop_app"'
assert_jq '.intent.app_level == "full_app"'
assert_jq '.intent.api_required == false'
assert_jq '.intent.frontend_required == false'
assert_jq '.requirements.stack.gui == "PySide6（Qt）"'
assert_jq '.requirements.logging.file_pattern == "logs/YYYY-MM-DD.md"'
assert_jq '.requirements.diff_detection.score_formula == "change_score = 1 - SSIM(gray(prev), gray(curr))"'
assert_jq '(. | has("api_contract")) == false'

# Case 11: Functional Intent dashboard backfills ai_task.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-11.md" <<'EOF'
# Functional Intent
health_recovery_dashboard
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "unknown",
  "ai_task": "unknown",
  "input_type": "unknown",
  "ui_type": "unknown"
}
EOF

cat > "$RUNTIME_DIR/intent_ir.json" <<'EOF'
{
  "artifact_targets": ["web_app"],
  "feature_intents": ["health_recovery_dashboard"],
  "clarify_required": false,
  "ambiguities": []
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.ai_task == "health_recovery_dashboard"'

# Case 12: UI Preference dashboard backfills web app/ui when unknown.
rm -f "$QUEUE_DIR"/*.md
rm -f "$RUNTIME_DIR/intent_ir.json" "$RUNTIME_DIR/.queue_target"
cat > "$QUEUE_DIR/test-case-12.md" <<'EOF'
# UI Preference
dashboard
EOF

cat > "$RUNTIME_DIR/interpret.json" <<'EOF'
{
  "project_type": "unknown",
  "ai_task": "unknown",
  "input_type": "unknown",
  "ui_type": "unknown"
}
EOF

bash ./factory/parser/build_spec.sh
assert_jq '.project_type == "web_app"'
assert_jq '.ui_type == "web_interface"'

echo "parser tests: OK"
