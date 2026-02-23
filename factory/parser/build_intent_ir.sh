#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
if [ ! -d "$QUEUE_DIR" ]; then
  exit 0
fi

shopt -s nullglob
queue_files=("$QUEUE_DIR"/*.md)
shopt -u nullglob
if [ "${#queue_files[@]}" -eq 0 ]; then
  exit 0
fi

mkdir -p runtime

python3 - <<'PY' > runtime/intent_ir.json
import glob
import json
import os
import re
import subprocess

interpret = {}
try:
    with open("runtime/interpret.json", "r", encoding="utf-8") as f:
        interpret = json.load(f)
except Exception:
    pass

target_hint = "runtime/.queue_target"
source_queue = ""
if os.path.exists(target_hint):
    try:
        hinted = open(target_hint, "r", encoding="utf-8").read().strip()
        if hinted and os.path.exists(hinted):
            source_queue = hinted
    except Exception:
        pass

if not source_queue:
    candidates = glob.glob(os.path.join("queue", "*.md"))
    if candidates:
        candidates.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
        source_queue = candidates[0]

raw_text = ""
if source_queue:
    try:
        raw_text = open(source_queue, "r", encoding="utf-8").read()
    except Exception:
        raw_text = ""

text = raw_text.lower().replace("&#x20;", " ").replace("\\_", "_")
raw_text_clean = raw_text.replace("&#x20;", " ").replace("\\_", "_")


def read_config(key: str, default: str) -> str:
    cfg = os.path.join("factory", "scripts", "config_get.py")
    if not os.path.exists(cfg):
        return default
    try:
        out = subprocess.check_output(["python3", cfg, key, default], text=True).strip()
        return out or default
    except Exception:
        return default


def has(pattern: str) -> bool:
    return bool(re.search(pattern, text))


def count(pattern: str) -> int:
    return len(re.findall(pattern, text))


def uniq(items):
    out = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def section_value(source: str, labels):
    for label in labels:
        pat = rf"(?:^|\n)\s*#+\s*{re.escape(label)}\s*\n+([^\n#]+)"
        m = re.search(pat, source, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


artifact_targets = []
if has(r"\bdesktop\b|\bpyside6\b|\bmss\b|デスクトップ|画面|スクリーン"):
    artifact_targets.append("desktop_gui_app")
if has(r"\bweb\b|\bfrontend\b|webアプリ|ブラウザ"):
    artifact_targets.append("web_app")
if has(r"\bcli\b"):
    artifact_targets.append("cli_tool")
if has(r"\bapi\b|\bendpoint\b"):
    artifact_targets.append("api_service")
if has(r"\bworker\b|\bdaemon\b|\bcron\b"):
    artifact_targets.append("worker")
artifact_targets = uniq(artifact_targets) or ["unknown"]

ui_pref = section_value(raw_text_clean, ["UI Preference", "UI設定", "UI"])
ui_pref_lc = ui_pref.lower()
if ui_pref_lc in ("dashboard", "web", "browser"):
    artifact_targets = uniq([x for x in artifact_targets if x != "unknown"] + ["web_app"]) or ["web_app"]
elif ui_pref_lc in ("desktop", "desktop_gui", "native"):
    artifact_targets = uniq([x for x in artifact_targets if x != "unknown"] + ["desktop_gui_app"]) or ["desktop_gui_app"]

feature_intents = []
if has(r"\bmonitor\b|監視"):
    feature_intents.append("monitor")
if has(r"\bdiff\b|差分|ssim"):
    feature_intents.append("diff_detection")
if has(r"markdown|マークダウン"):
    feature_intents.append("markdown_logging")
if has(r"notify|notification|通知"):
    feature_intents.append("notification")
if has(r"auth|oauth|認証"):
    feature_intents.append("auth")
if has(r"payment|stripe|決済"):
    feature_intents.append("payment")
feature_intents = uniq(feature_intents)

functional_intent = section_value(raw_text_clean, ["Functional Intent", "機能意図", "Intent"])
if functional_intent:
    normalized_intent = re.sub(r"[^a-zA-Z0-9]+", "_", functional_intent).strip("_").lower()
    if normalized_intent:
        feature_intents = uniq([normalized_intent] + feature_intents)
        if "dashboard" in normalized_intent and "web_app" not in artifact_targets:
            artifact_targets = uniq([x for x in artifact_targets if x != "unknown"] + ["web_app"]) or ["web_app"]

stack_hints = {}
if has(r"\bpython\b"):
    stack_hints["language"] = "python"
if has(r"\bnode\b|\bnpm\b|\bexpress\b"):
    stack_hints["language"] = "javascript"
if has(r"\bpyside6\b"):
    stack_hints["gui"] = "pyside6"
if has(r"\bmss\b"):
    stack_hints["capture"] = "mss"
if has(r"\bopencv\b"):
    stack_hints["image"] = "opencv"
if has(r"\bscikit-image\b|\bssim\b"):
    stack_hints["ssim"] = "scikit-image"
if has(r"\boffline\b|ローカル|外部送信しない"):
    stack_hints["offline_preferred"] = True

contracts_behaviors = []
if "diff_detection" in feature_intents:
    contracts_behaviors.append("compute diff score and threshold")
if "markdown_logging" in feature_intents:
    contracts_behaviors.append("append markdown logs")
if "notification" in feature_intents:
    contracts_behaviors.append("emit local notification")

explicit_requirements = bool(re.search(r"(project_type|ai_task|input_type|ui_type)\s*:", text))
default_target = read_config("default_artifact_target", "web_app").strip().lower()
if artifact_targets == ["unknown"] and default_target in ("web_app", "desktop_app"):
    artifact_targets = [default_target if default_target != "desktop_app" else "desktop_gui_app"]

contracts_files = []
if "desktop_gui_app" in artifact_targets:
    contracts_files.extend(
        [
            "core/app.py",
            "core/ui/main_window.py",
            "core/services/diff_detector.py",
            "core/services/markdown_logger.py",
            "README.md",
        ]
    )
if "web_app" in artifact_targets:
    contracts_files.extend(
        [
            "core/server.js",
            "core/public/index.html",
            "core/public/app.js",
            "README.md",
        ]
    )
if "cli_tool" in artifact_targets:
    contracts_files.extend(["core/main.py", "README.md"])

ambiguities = []
if "unknown" in artifact_targets:
    ambiguities.append("artifact target is unknown")
if len([x for x in ("desktop_gui_app", "web_app", "cli_tool") if x in artifact_targets]) > 1:
    ambiguities.append("multiple UI modalities requested")

confidence = 0.45
if explicit_requirements:
    confidence += 0.35
if "unknown" not in artifact_targets:
    confidence += 0.15
if functional_intent:
    confidence += 0.10
if ui_pref:
    confidence += 0.05
if ambiguities:
    confidence -= 0.25
confidence = max(0.0, min(1.0, confidence))
clarify_required = confidence < 0.6 or bool(ambiguities)

legacy_projection = {
    "project_type": str(interpret.get("project_type", "unknown")),
    "ai_task": str(interpret.get("ai_task", "unknown")),
    "input_type": str(interpret.get("input_type", "unknown")),
    "ui_type": str(interpret.get("ui_type", "unknown")),
}

ir = {
    "version": "1",
    "source_queue": source_queue or "none",
    "artifact_targets": artifact_targets,
    "feature_intents": feature_intents,
    "stack_hints": stack_hints,
    "contracts": {
        "files": uniq(contracts_files),
        "behaviors": uniq(contracts_behaviors),
        "test_rules": ["syntax", "smoke"] if contracts_files else [],
    },
    "confidence": round(confidence, 3),
    "ambiguities": ambiguities,
    "clarify_required": clarify_required,
    "legacy_projection": legacy_projection,
}

print(json.dumps(ir, ensure_ascii=False, indent=2))
PY
