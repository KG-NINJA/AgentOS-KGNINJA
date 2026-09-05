#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
RUNTIME_DIR="runtime"
OUTPUT_FILE="$RUNTIME_DIR/interpret.json"
PROMPT_FILE="$RUNTIME_DIR/.interpret_prompt.txt"
SCHEMA_FILE="$RUNTIME_DIR/.interpret_schema.json"
RAW_FILE="$RUNTIME_DIR/.interpret_raw.json"

if [ ! -d "$QUEUE_DIR" ]; then
  exit 0
fi

shopt -s nullglob
queue_files=("$QUEUE_DIR"/*.md)
shopt -u nullglob

if [ "${#queue_files[@]}" -eq 0 ]; then
  exit 0
fi

target_file="$(python3 - "$QUEUE_DIR" <<'PY'
import glob
import os
import sys

queue_dir = sys.argv[1]
files = glob.glob(os.path.join(queue_dir, "*.md"))
if not files:
    sys.exit(1)

# Prefer newest queue item; tie-break by path for deterministic ordering.
files.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
print(files[0])
PY
)"

mkdir -p "$RUNTIME_DIR"
printf '%s\n' "$target_file" > "$RUNTIME_DIR/.queue_target"

write_fallback() {
  python3 - "$target_file" > "$OUTPUT_FILE" <<'PY'
import json
import re
import sys

path = sys.argv[1]
try:
    raw_text = open(path, "r", encoding="utf-8").read()
except Exception:
    raw_text = ""

# Normalize common escaped markdown/html artifacts.
text = raw_text.lower().replace("&#x20;", " ").replace("\\_", "_")

def has(pattern):
    return bool(re.search(pattern, text))

def extract_structured(source, key):
    escaped = key.replace("_", r"\\_")
    m = re.search(rf"(?:^|\n)\s*(?:{key}|{escaped})\s*:\s*([a-z0-9_/-]+)", source)
    return m.group(1).strip() if m else None

def count_hits(pattern):
    return len(re.findall(pattern, text))

def desktop_signal_score():
    return (
        count_hits(r"\bdesktop\b")
        + count_hits(r"\bscreen\b")
        + count_hits(r"\bmonitor\b")
        + count_hits(r"\bdiff\b")
        + count_hits(r"\bmarkdown\b")
        + count_hits(r"\bpyside6\b")
        + count_hits(r"\bmss\b")
        + count_hits(r"\bopencv\b")
        + count_hits(r"\bscikit-image\b")
        + count_hits(r"\bssim\b")
        + count_hits(r"デスクトップ|画面|スクリーン|監視|差分|マークダウン|通知")
    )

def web_signal_score():
    return (
        count_hits(r"\bweb_app\b")
        + count_hits(r"\bweb app\b")
        + count_hits(r"\bweb_interface\b")
        + count_hits(r"\bweb\b")
        + count_hits(r"ウェブ|webアプリ|ブラウザ")
    )

out = {
    "project_type": "unknown",
    "ai_task": "unknown",
    "input_type": "unknown",
    "ui_type": "unknown",
}

structured_project_type = extract_structured(text, "project_type")
if structured_project_type:
    out["project_type"] = structured_project_type

structured_ai_task = extract_structured(text, "ai_task")
if structured_ai_task:
    out["ai_task"] = structured_ai_task

structured_input_type = extract_structured(text, "input_type")
if structured_input_type:
    out["input_type"] = structured_input_type

structured_ui_type = extract_structured(text, "ui_type")
if structured_ui_type:
    out["ui_type"] = structured_ui_type

desktop_score = desktop_signal_score()
web_score = web_signal_score()
desktop_strong = desktop_score >= 3 and desktop_score >= web_score

if out["project_type"] == "unknown":
    if desktop_strong or has(r"\bpyside6\b|\bmss\b|\bssim\b") or has(r"デスクトップ|画面監視|差分検出"):
        out["project_type"] = "desktop_app"
    elif has(r"\bweb_app\b") or has(r"\bweb app\b") or has(r"\bweb\b") or has(r"ウェブ|webアプリ"):
        out["project_type"] = "web_app"
    elif has(r"\bcli\b"):
        out["project_type"] = "cli_tool"

if out["ai_task"] == "unknown":
    if has(r"\bmonitor\b|\bwatch\b|\bdiff\b|\bssim\b") or has(r"監視|差分|変化"):
        out["ai_task"] = "screen_change_monitoring"

if out["input_type"] == "unknown":
    if has(r"\bscreen_capture\b|\bscreenshot\b|\bdisplay\b|\bregion\b|\bmss\b") or has(r"画面|スクリーン|矩形領域"):
        out["input_type"] = "screen_capture"
    elif has(r"\bvoice_input\b") or has(r"\bvoice\b") or has(r"\baudio\b") or has(r"音声|ボイス"):
        out["input_type"] = "voice_input"
    elif has(r"\btext_input\b") or has(r"\btext\b"):
        out["input_type"] = "text_input"

if out["ui_type"] == "unknown":
    if out["project_type"] == "desktop_app" or has(r"\bpyside6\b|\bqt\b|\bdesktop gui\b") or has(r"デスクトップGUI|GUI"):
        out["ui_type"] = "desktop_gui"
    elif has(r"\bweb_interface\b") or has(r"\bweb ui\b") or has(r"\bweb\b") or has(r"ウェブ|ブラウザ|web"):
        out["ui_type"] = "web_interface"
    elif has(r"\bcli\b"):
        out["ui_type"] = "cli"

print(json.dumps(out, ensure_ascii=False, indent=2))
PY
}

if command -v codex >/dev/null 2>&1; then
  cat > "$SCHEMA_FILE" <<'EOF'
{
  "type": "object",
  "required": ["project_type", "ai_task", "input_type", "ui_type"],
  "properties": {
    "project_type": {"type": "string"},
    "ai_task": {"type": "string"},
    "input_type": {"type": "string"},
    "ui_type": {"type": "string"},
    "intent": {
      "type": "object",
      "properties": {
        "app_level": {"type": "string", "enum": ["full_app", "module"]},
        "api_required": {"type": "boolean"},
        "frontend_required": {"type": "boolean"}
      },
      "additionalProperties": false
    },
    "ui_layout": {
      "type": "object",
      "properties": {
        "controls": {"type": "array", "items": {"type": "string"}},
        "output": {"type": "string"}
      },
      "additionalProperties": false
    },
    "api_contract": {
      "type": "object",
      "properties": {
        "endpoint": {"type": "string"},
        "method": {"type": "string"},
        "response": {"type": "string"}
      },
      "additionalProperties": false
    },
    "runtime_rules": {
      "type": "object"
    }
  },
  "additionalProperties": false
}
EOF

  cat > "$PROMPT_FILE" <<EOF
Classify the following project idea text and return only JSON with exactly these fields:
- project_type
- ai_task
- input_type
- ui_type
- optional: intent, ui_layout, api_contract, runtime_rules

Use concise snake_case strings.
If unknown, use "unknown".
Only add optional objects if confidence is high.

Project idea:
$(cat "$target_file")
EOF

  if python3 "$ROOT/factory/agent/codex_runtime.py" --role interpretation -- exec --output-schema "$SCHEMA_FILE" --output-last-message "$RAW_FILE" - < "$PROMPT_FILE" >/dev/null 2>&1; then
    if python3 - "$RAW_FILE" "$target_file" <<'PY' > "$OUTPUT_FILE"
import json
import re
import sys

raw_path = sys.argv[1]
target_path = sys.argv[2]
text = open(raw_path, "r", encoding="utf-8").read().strip()
obj = None

try:
    obj = json.loads(text)
except Exception:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        obj = json.loads(m.group(0))

if not isinstance(obj, dict):
    raise ValueError("invalid output")

keys = ["project_type", "ai_task", "input_type", "ui_type"]
out = {}
for k in keys:
    v = obj.get(k, "unknown")
    if v is None:
        v = "unknown"
    v = str(v).strip()
    out[k] = v if v else "unknown"

try:
    raw_source_text = open(target_path, "r", encoding="utf-8").read()
except Exception:
    raw_source_text = ""

# Normalize common escaped markdown/html artifacts.
source_text = raw_source_text.lower().replace("&#x20;", " ").replace("\\_", "_")

def has(pattern):
    return bool(re.search(pattern, source_text))

def extract_structured(source, key):
    escaped = key.replace("_", r"\\_")
    m = re.search(rf"(?:^|\n)\s*(?:{key}|{escaped})\s*:\s*([a-z0-9_/-]+)", source)
    return m.group(1).strip() if m else None

def count_hits(pattern):
    return len(re.findall(pattern, source_text))

def desktop_signal_score():
    return (
        count_hits(r"\bdesktop\b")
        + count_hits(r"\bscreen\b")
        + count_hits(r"\bmonitor\b")
        + count_hits(r"\bdiff\b")
        + count_hits(r"\bmarkdown\b")
        + count_hits(r"\bpyside6\b")
        + count_hits(r"\bmss\b")
        + count_hits(r"\bopencv\b")
        + count_hits(r"\bscikit-image\b")
        + count_hits(r"\bssim\b")
        + count_hits(r"デスクトップ|画面|スクリーン|監視|差分|マークダウン|通知")
    )

def web_signal_score():
    return (
        count_hits(r"\bweb_app\b")
        + count_hits(r"\bweb app\b")
        + count_hits(r"\bweb_interface\b")
        + count_hits(r"\bweb\b")
        + count_hits(r"ウェブ|webアプリ|ブラウザ")
    )

desktop_score = desktop_signal_score()
web_score = web_signal_score()
desktop_strong = desktop_score >= 3 and desktop_score >= web_score

if out["project_type"] == "unknown":
    structured = extract_structured(source_text, "project_type")
    if structured:
        out["project_type"] = structured
if out["project_type"] in ("unknown", "web_app"):
    if desktop_strong or has(r"\bpyside6\b|\bmss\b|\bssim\b") or has(r"デスクトップ|画面監視|差分検出"):
        out["project_type"] = "desktop_app"
    elif out["project_type"] == "unknown" and (has(r"\bweb_app\b") or has(r"\bweb app\b") or has(r"\bweb\b") or has(r"ウェブ|webアプリ")):
        out["project_type"] = "web_app"
    elif out["project_type"] == "unknown" and has(r"\bcli\b"):
        out["project_type"] = "cli_tool"

if out["ai_task"] == "unknown":
    structured = extract_structured(source_text, "ai_task")
    if structured:
        out["ai_task"] = structured
if out["ai_task"] == "unknown":
    if has(r"\bmonitor\b|\bwatch\b|\bdiff\b|\bssim\b") or has(r"監視|差分|変化"):
        out["ai_task"] = "screen_change_monitoring"

if out["input_type"] == "unknown":
    structured = extract_structured(source_text, "input_type")
    if structured:
        out["input_type"] = structured
if out["input_type"] == "unknown":
    if has(r"\bscreen_capture\b|\bscreenshot\b|\bdisplay\b|\bregion\b|\bmss\b") or has(r"画面|スクリーン|矩形領域"):
        out["input_type"] = "screen_capture"
    elif has(r"\bvoice_input\b") or has(r"\bvoice\b") or has(r"\baudio\b") or has(r"音声|ボイス"):
        out["input_type"] = "voice_input"
    elif has(r"\btext_input\b") or has(r"\btext\b"):
        out["input_type"] = "text_input"

if out["ui_type"] == "unknown":
    structured = extract_structured(source_text, "ui_type")
    if structured:
        out["ui_type"] = structured
if out["ui_type"] in ("unknown", "web_interface"):
    if out["project_type"] == "desktop_app" or has(r"\bpyside6\b|\bqt\b|\bdesktop gui\b") or has(r"デスクトップGUI|GUI"):
        out["ui_type"] = "desktop_gui"
    elif out["ui_type"] == "unknown" and (has(r"\bweb_interface\b") or has(r"\bweb ui\b") or has(r"\bweb\b") or has(r"ウェブ|ブラウザ|web")):
        out["ui_type"] = "web_interface"
    elif out["ui_type"] == "unknown" and has(r"\bcli\b"):
        out["ui_type"] = "cli"

intent = obj.get("intent")
if isinstance(intent, dict):
    cleaned_intent = {}
    app_level = intent.get("app_level")
    if app_level in ("full_app", "module"):
        cleaned_intent["app_level"] = app_level
    api_required = intent.get("api_required")
    if isinstance(api_required, bool):
        cleaned_intent["api_required"] = api_required
    frontend_required = intent.get("frontend_required")
    if isinstance(frontend_required, bool):
        cleaned_intent["frontend_required"] = frontend_required
    if cleaned_intent:
        out["intent"] = cleaned_intent

ui_layout = obj.get("ui_layout")
if isinstance(ui_layout, dict):
    cleaned_layout = {}
    controls = ui_layout.get("controls")
    if isinstance(controls, list) and all(isinstance(x, str) for x in controls):
        cleaned_layout["controls"] = controls
    output = ui_layout.get("output")
    if isinstance(output, str):
        cleaned_layout["output"] = output
    if cleaned_layout:
        out["ui_layout"] = cleaned_layout

api_contract = obj.get("api_contract")
if isinstance(api_contract, dict):
    cleaned_contract = {}
    endpoint = api_contract.get("endpoint")
    if isinstance(endpoint, str):
        cleaned_contract["endpoint"] = endpoint
    method = api_contract.get("method")
    if isinstance(method, str):
        cleaned_contract["method"] = method
    response = api_contract.get("response")
    if isinstance(response, str):
        cleaned_contract["response"] = response
    if cleaned_contract:
        out["api_contract"] = cleaned_contract

runtime_rules = obj.get("runtime_rules")
if isinstance(runtime_rules, dict):
    out["runtime_rules"] = runtime_rules

print(json.dumps(out, ensure_ascii=False, indent=2))
PY
    then
      rm -f "$PROMPT_FILE" "$SCHEMA_FILE" "$RAW_FILE"
      exit 0
    fi
  fi
fi

if [ "${FACTORY_CODEX_PROFILE:-legacy}" != "legacy" ]; then
  echo "fail_reason=gpt6-interpretation-unverified" >&2
  exit 78
fi
write_fallback
rm -f "$PROMPT_FILE" "$SCHEMA_FILE" "$RAW_FILE"
