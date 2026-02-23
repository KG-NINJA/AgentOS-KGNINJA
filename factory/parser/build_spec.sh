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

python3 - <<'PY' > runtime/spec.json
import glob
import json
import os
import re
import sys

schema_path = "factory/parser/schema.json"
if not os.path.exists(schema_path):
    sys.exit(1)

with open(schema_path, "r", encoding="utf-8") as f:
    schema = json.load(f)

try:
    with open("runtime/interpret.json", "r", encoding="utf-8") as f:
        interpret = json.load(f)
except Exception:
    interpret = {}

try:
    with open("runtime/entities.json", "r", encoding="utf-8") as f:
        entities = json.load(f)
except Exception:
    entities = {}

try:
    with open("runtime/intent_ir.json", "r", encoding="utf-8") as f:
        intent_ir = json.load(f)
except Exception:
    intent_ir = {}

spec = {
    "project_type": str(interpret.get("project_type", "unknown")),
    "ai_task": str(interpret.get("ai_task", "unknown")),
    "input_type": str(interpret.get("input_type", "unknown")),
    "ui_type": str(interpret.get("ui_type", "unknown")),
    "entities": {
        "noun": list(entities.get("noun", [])),
        "verb": list(entities.get("verb", [])),
        "modifier": list(entities.get("modifier", [])),
    },
}

source_queue = ""
target_hint = "runtime/.queue_target"
if os.path.exists(target_hint):
    try:
        hinted = open(target_hint, "r", encoding="utf-8").read().strip()
        queue_root = os.path.abspath("queue")
        hinted_abs = os.path.abspath(hinted) if hinted else ""
        if (
            hinted
            and os.path.exists(hinted_abs)
            and hinted_abs.startswith(queue_root + os.sep)
            and hinted_abs.endswith(".md")
        ):
            source_queue = hinted_abs
    except Exception:
        pass

if not source_queue:
    queue_candidates = glob.glob(os.path.join("queue", "*.md"))
    if queue_candidates:
        queue_candidates.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
        source_queue = queue_candidates[0]

queue_text_raw = ""
if source_queue:
    try:
        with open(source_queue, "r", encoding="utf-8") as f:
            queue_text_raw = f.read()
    except Exception:
        queue_text_raw = ""

queue_text_lower = queue_text_raw.lower().replace("&#x20;", " ").replace("\\_", "_")


def count_hits(pattern):
    return len(re.findall(pattern, queue_text_lower))


def extract_structured(source, key):
    escaped = key.replace("_", r"\\_")
    m = re.search(rf"(?:^|\n)\s*(?:{key}|{escaped})\s*:\s*([a-z0-9_/-]+)", source)
    return m.group(1).strip() if m else None


web_hits = count_hits(r"\bweb\b") + count_hits(r"ウェブ|webアプリ|web_app|ブラウザ")
desktop_hits = (
    count_hits(r"\bdesktop\b")
    + count_hits(r"\bscreen\b")
    + count_hits(r"\bmonitor\b")
    + count_hits(r"\bdiff\b")
    + count_hits(r"\bpyside6\b")
    + count_hits(r"\bmss\b")
    + count_hits(r"\bopencv\b")
    + count_hits(r"\bssim\b")
    + count_hits(r"デスクトップ|画面|スクリーン|監視|差分")
)
cli_hits = count_hits(r"\bcli\b")
voice_hits = count_hits(r"\bvoice\b") + count_hits(r"音声|ボイス|audio")
screen_input_hits = count_hits(r"\bscreen_capture\b|\bscreenshot\b|\bdisplay\b|\bregion\b|\bmss\b") + count_hits(r"画面|スクリーン|矩形領域")
text_hits = count_hits(r"\btext\b|\btext_input\b")
generate_hits = count_hits(r"\bgenerate\b") + count_hits(r"生成|作成")
api_hits = count_hits(r"\bapi\b")
frontend_hits = count_hits(r"\bfrontend\b") + count_hits(r"フロントエンド|ui")
monitor_hits = count_hits(r"\bmonitor\b|\bwatch\b|\bdiff\b|\bssim\b") + count_hits(r"監視|差分|変化")

neg_web = bool(re.search(r"\b(no\s+web|without\s+web|cli\s+only)\b", queue_text_lower) or re.search(r"web不要|ウェブ不要|cliのみ", queue_text_lower))
neg_frontend = bool(re.search(r"\b(no\s+frontend|without\s+frontend|backend\s+only)\b", queue_text_lower) or re.search(r"frontend不要|フロントエンド不要|バックエンドのみ", queue_text_lower))
neg_api = bool(re.search(r"\b(no\s+api|without\s+api|frontend\s+only)\b", queue_text_lower) or re.search(r"api不要|フロントエンドのみ", queue_text_lower))

web_score = (2 * web_hits) + (2 * generate_hits) + api_hits + frontend_hits
desktop_score = (2 * desktop_hits) + monitor_hits
if neg_web:
    web_score -= 3
if neg_frontend:
    web_score -= 3
if neg_api:
    web_score -= 3

infer_full_app = web_score >= 3 and web_score >= desktop_score and not (neg_web or neg_frontend or neg_api)

if spec["project_type"] == "unknown":
    if desktop_score >= 3 and desktop_score >= web_score:
        spec["project_type"] = "desktop_app"
    elif infer_full_app or (web_hits > 0 and not neg_web):
        spec["project_type"] = "web_app"
    elif cli_hits > 0:
        spec["project_type"] = "cli_tool"

if spec["ai_task"] == "unknown":
    if monitor_hits > 0 and desktop_score > 0:
        spec["ai_task"] = "screen_change_monitoring"
    else:
        ir_features = intent_ir.get("feature_intents") if isinstance(intent_ir, dict) else []
        if isinstance(ir_features, list):
            for feat in ir_features:
                if not isinstance(feat, str):
                    continue
                if feat.endswith("_dashboard") or feat == "health_recovery_dashboard":
                    spec["ai_task"] = feat
                    break

if spec["input_type"] == "unknown":
    if screen_input_hits > 0 and desktop_score > 0:
        spec["input_type"] = "screen_capture"
    elif voice_hits > 0:
        spec["input_type"] = "voice_input"
    elif text_hits > 0:
        spec["input_type"] = "text_input"

if spec["ui_type"] == "unknown":
    if spec["project_type"] == "desktop_app" or desktop_score >= 3:
        spec["ui_type"] = "desktop_gui"
    elif infer_full_app or (web_hits > 0 and not neg_web):
        spec["ui_type"] = "web_interface"
    elif cli_hits > 0:
        spec["ui_type"] = "cli"

if spec["project_type"] == "unknown":
    ui_pref = ""
    m = re.search(r"(?:^|\n)\s*#+\s*UI Preference\s*\n+([^\n#]+)", queue_text_raw, re.IGNORECASE)
    if m:
        ui_pref = m.group(1).strip().lower()
    if ui_pref in ("dashboard", "web", "browser"):
        spec["project_type"] = "web_app"
        if spec["ui_type"] == "unknown":
            spec["ui_type"] = "web_interface"


def as_string(value):
    return value if isinstance(value, str) else None


def as_bool(value):
    return value if isinstance(value, bool) else None


def as_string_array(value):
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return value


def as_int(value, min_value=None, max_value=None):
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if min_value is not None and value < min_value:
        return None
    if max_value is not None and value > max_value:
        return None
    return value


def as_enum_string(value, allowed):
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    return norm if norm in allowed else None


def is_json_safe(value, depth=0):
    if depth > 5:
        return False
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        if len(value) > 64:
            return False
        return all(is_json_safe(item, depth + 1) for item in value)
    if isinstance(value, dict):
        if len(value) > 64:
            return False
        for key, item in value.items():
            if not isinstance(key, str):
                return False
            if not is_json_safe(item, depth + 1):
                return False
        return True
    return False


def sanitize_runtime_rules(value):
    if not isinstance(value, dict):
        return None

    out = {}
    stability_priority = as_string(value.get("stability_priority"))
    if stability_priority in ("low_ambiguity", "balanced", "creative"):
        out["stability_priority"] = stability_priority

    deterministic = as_bool(value.get("deterministic"))
    if deterministic is not None:
        out["deterministic"] = deterministic

    strict_mode = as_bool(value.get("strict_mode"))
    if strict_mode is not None:
        out["strict_mode"] = strict_mode

    timeout_ms = as_int(value.get("timeout_ms"), min_value=100, max_value=120000)
    if timeout_ms is not None:
        out["timeout_ms"] = timeout_ms

    retry_policy = value.get("retry_policy")
    if isinstance(retry_policy, dict):
        retry_out = {}
        max_retries = as_int(retry_policy.get("max_retries"), min_value=0, max_value=8)
        if max_retries is not None:
            retry_out["max_retries"] = max_retries
        backoff_ms = as_int(retry_policy.get("backoff_ms"), min_value=0, max_value=60000)
        if backoff_ms is not None:
            retry_out["backoff_ms"] = backoff_ms
        if retry_out:
            out["retry_policy"] = retry_out

    for key, item in value.items():
        if re.match(r"^x_[A-Za-z0-9_]{1,64}$", key) and is_json_safe(item):
            out[key] = item

    return out if out else None


def clean_markdown_text(value):
    out = value.replace("&#x20;", " ")
    out = out.replace("\\_", "_")
    out = out.replace("\\#", "#")
    out = out.replace("\\-", "-")
    out = out.replace("\\*", "*")
    out = out.replace("\\.", ".")
    return out


def normalize_list_line(line):
    s = line.strip()
    s = re.sub(r"^[\-\*\u2022]\s*", "", s)
    s = re.sub(r"^\d+\.\s*", "", s)
    s = s.replace("**", "")
    s = s.replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_stack_fields(lines):
    out = {}
    mappings = {
        "language": [r"言語\s*[:：]\s*(.+)", r"language\s*[:：]\s*(.+)"],
        "gui": [r"gui\s*[:：]\s*(.+)", r"ui\s*[:：]\s*(.+)"],
        "capture": [r"画面キャプチャ\s*[:：]\s*(.+)", r"capture\s*[:：]\s*(.+)"],
        "image": [r"画像処理\s*[:：]\s*(.+)", r"image\s*[:：]\s*(.+)"],
        "ssim": [r"ssim\s*[:：]\s*(.+)"],
    }
    for line in lines:
        plain = normalize_list_line(line)
        lower = plain.lower()
        for key, patterns in mappings.items():
            if key in out:
                continue
            for pattern in patterns:
                m = re.search(pattern, plain, re.IGNORECASE)
                if m:
                    out[key] = m.group(1).strip()
                    break
        if "pyside6" in lower and "gui" not in out:
            out["gui"] = "PySide6"
        if "mss" in lower and "capture" not in out:
            out["capture"] = "mss"
        if "opencv" in lower and "image" not in out:
            out["image"] = "opencv-python"
        if "scikit-image" in lower and "ssim" not in out:
            out["ssim"] = "scikit-image"
    return out


def parse_requirements(raw_text):
    cleaned = clean_markdown_text(raw_text)
    lines = cleaned.splitlines()
    requirements = {
        "use_cases": [],
        "mvp_features": [],
        "non_functional": [],
        "data_requirements": [],
        "interaction_model": [],
        "priority": "",
        "risk_level": "",
        "diff_detection": {"methods": [], "noise_filters": []},
        "markdown_output": {"fields": []},
        "logging": {},
        "notifications": [],
        "stack": {},
        "directory_structure": [],
    }

    section = ""
    stack_lines = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            h = stripped.lower()
            if "ユースケース" in stripped or "problem framing" in h:
                section = "use_cases"
            elif "mvpでやること" in stripped or "機能" in stripped or "recommended actions" in h:
                section = "mvp_features"
            elif "非機能" in stripped:
                section = "non_functional"
            elif "data requirements" in h or "データ要件" in stripped:
                section = "data_requirements"
            elif "interaction model" in h or "インタラクション" in stripped:
                section = "interaction_model"
            elif "priority" in h or "優先度" in stripped:
                section = "priority"
            elif "risk level" in h or "リスクレベル" in stripped:
                section = "risk_level"
            elif "目立つ変化" in stripped or "差分スコア" in stripped:
                section = "diff_detection"
            elif "markdownで説明" in stripped:
                section = "markdown_output"
            elif "ログ保存" in stripped:
                section = "logging"
            elif "通知" in stripped:
                section = "notifications"
            elif "技術スタック" in stripped:
                section = "stack"
            elif "ディレクトリ構成" in stripped:
                section = "directory_structure"
            else:
                section = ""
            continue

        if section == "":
            continue

        line = normalize_list_line(stripped)
        if not line:
            continue
        lower = line.lower()

        if section == "use_cases":
            requirements["use_cases"].append(line)
            continue

        if section == "mvp_features":
            requirements["mvp_features"].append(line)
            if "logs/yyyy-mm-dd.md" in lower:
                requirements["logging"]["file_pattern"] = "logs/YYYY-MM-DD.md"
                requirements["markdown_output"]["storage_pattern"] = "append daily markdown logs"
            if "logs/assets/yyyy-mm-dd/" in lower:
                requirements["logging"]["asset_dir"] = "logs/assets/YYYY-MM-DD/"
            continue

        if section == "non_functional":
            requirements["non_functional"].append(line)
            continue
        if section == "data_requirements":
            requirements["data_requirements"].append(line)
            continue
        if section == "interaction_model":
            requirements["interaction_model"].append(line)
            continue
        if section == "priority":
            requirements["priority"] = line
            continue
        if section == "risk_level":
            requirements["risk_level"] = lower
            continue

        if section == "diff_detection":
            if "ssim" in lower:
                requirements["diff_detection"]["methods"].append("ssim")
            if "ピクセル差分" in line or "pixel" in lower:
                requirements["diff_detection"]["methods"].append("pixel_diff")
            if "ヒストグラム" in line or "histogram" in lower:
                requirements["diff_detection"]["methods"].append("histogram_diff")
            if "change_score" in lower or "ssim(gray(prev), gray(curr))" in lower:
                requirements["diff_detection"]["score_formula"] = "change_score = 1 - SSIM(gray(prev), gray(curr))"
            if "縮小" in line or "gaussian" in lower or "ガウシアン" in line or "面積" in line:
                requirements["diff_detection"]["noise_filters"].append(line)
            continue

        if section == "markdown_output":
            field_map = [
                ("timestamp", ["タイムスタンプ", "timestamp"]),
                ("summary", ["要約"]),
                ("change_score", ["変化量", "score"]),
                ("snapshots", ["スナップショット", "before/after"]),
                ("heatmap", ["ヒートマップ", "heatmap"]),
            ]
            for field_name, words in field_map:
                if any(word.lower() in lower for word in words):
                    requirements["markdown_output"]["fields"].append(field_name)
            continue

        if section == "logging":
            if "logs/yyyy-mm-dd.md" in lower:
                requirements["logging"]["file_pattern"] = "logs/YYYY-MM-DD.md"
            if "logs/assets/yyyy-mm-dd/" in lower:
                requirements["logging"]["asset_dir"] = "logs/assets/YYYY-MM-DD/"
            continue

        if section == "notifications":
            requirements["notifications"].append(line)
            continue

        if section == "stack":
            stack_lines.append(line)
            continue

        if section == "directory_structure":
            requirements["directory_structure"].append(line)

    lowered_cleaned = cleaned.lower()
    if re.search(r"logs\s*/\s*yyyy-mm-dd\.md", lowered_cleaned):
        requirements["logging"]["file_pattern"] = "logs/YYYY-MM-DD.md"
        requirements["markdown_output"]["storage_pattern"] = "append daily markdown logs"
    if re.search(r"logs\s*/\s*assets\s*/\s*yyyy-mm-dd\s*/", lowered_cleaned):
        requirements["logging"]["asset_dir"] = "logs/assets/YYYY-MM-DD/"

    requirements["stack"] = parse_stack_fields(stack_lines)

    def uniq(items):
        out = []
        seen = set()
        for item in items:
            key = item.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    requirements["use_cases"] = uniq(requirements["use_cases"])
    requirements["mvp_features"] = uniq(requirements["mvp_features"])
    requirements["non_functional"] = uniq(requirements["non_functional"])
    requirements["data_requirements"] = uniq(requirements["data_requirements"])
    requirements["interaction_model"] = uniq(requirements["interaction_model"])
    requirements["notifications"] = uniq(requirements["notifications"])
    requirements["directory_structure"] = uniq(requirements["directory_structure"])
    requirements["diff_detection"]["methods"] = uniq(requirements["diff_detection"]["methods"])
    requirements["diff_detection"]["noise_filters"] = uniq(requirements["diff_detection"]["noise_filters"])
    requirements["markdown_output"]["fields"] = uniq(requirements["markdown_output"]["fields"])

    compact = {}
    if requirements["use_cases"]:
        compact["use_cases"] = requirements["use_cases"]
    if requirements["mvp_features"]:
        compact["mvp_features"] = requirements["mvp_features"]
    if requirements["non_functional"]:
        compact["non_functional"] = requirements["non_functional"]
    if requirements["data_requirements"]:
        compact["data_requirements"] = requirements["data_requirements"]
    if requirements["interaction_model"]:
        compact["interaction_model"] = requirements["interaction_model"]
    if requirements["priority"]:
        compact["priority"] = requirements["priority"]
    if requirements["risk_level"]:
        compact["risk_level"] = requirements["risk_level"]
    if requirements["notifications"]:
        compact["notifications"] = requirements["notifications"]
    if requirements["directory_structure"]:
        compact["directory_structure"] = requirements["directory_structure"]
    if requirements["stack"]:
        compact["stack"] = requirements["stack"]

    diff_detection = {}
    if requirements["diff_detection"]["methods"]:
        diff_detection["methods"] = requirements["diff_detection"]["methods"]
    if requirements["diff_detection"].get("score_formula"):
        diff_detection["score_formula"] = requirements["diff_detection"]["score_formula"]
    if requirements["diff_detection"]["noise_filters"]:
        diff_detection["noise_filters"] = requirements["diff_detection"]["noise_filters"]
    if diff_detection:
        compact["diff_detection"] = diff_detection

    markdown_output = {}
    if requirements["markdown_output"]["fields"]:
        markdown_output["fields"] = requirements["markdown_output"]["fields"]
    if requirements["markdown_output"].get("storage_pattern"):
        markdown_output["storage_pattern"] = requirements["markdown_output"]["storage_pattern"]
    if markdown_output:
        compact["markdown_output"] = markdown_output

    if requirements["logging"]:
        compact["logging"] = requirements["logging"]

    return compact


intent_src = interpret.get("intent", {})
if not isinstance(intent_src, dict):
    intent_src = {}

intent = {}
app_level = as_string(intent_src.get("app_level"))
if app_level in ("full_app", "module"):
    intent["app_level"] = app_level

api_required = as_bool(intent_src.get("api_required"))
if api_required is not None:
    intent["api_required"] = api_required

frontend_required = as_bool(intent_src.get("frontend_required"))
if frontend_required is not None:
    intent["frontend_required"] = frontend_required

if infer_full_app:
    intent["app_level"] = "full_app"
    intent["api_required"] = True
    intent["frontend_required"] = True
elif spec["project_type"] == "desktop_app":
    intent.setdefault("app_level", "full_app")
    intent.setdefault("api_required", False)
    intent.setdefault("frontend_required", False)

if intent:
    spec["intent"] = intent

ui_layout_src = interpret.get("ui_layout", {})
if not isinstance(ui_layout_src, dict):
    ui_layout_src = {}

ui_layout = {}
controls = as_string_array(ui_layout_src.get("controls"))
if controls is not None:
    ui_layout["controls"] = controls

output = as_string(ui_layout_src.get("output"))
if output is not None:
    ui_layout["output"] = output

if ui_layout:
    spec["ui_layout"] = ui_layout

api_contract_src = interpret.get("api_contract", {})
if not isinstance(api_contract_src, dict):
    api_contract_src = {}

api_contract = {}
endpoint = as_string(api_contract_src.get("endpoint"))
method = as_string(api_contract_src.get("method"))
response = as_string(api_contract_src.get("response"))

has_explicit_api_contract = bool(endpoint or method or response)
if spec["project_type"] == "web_app" or has_explicit_api_contract:
    if endpoint is not None:
        endpoint = endpoint.strip()
        if not endpoint:
            endpoint = "/api/generate"
    else:
        endpoint = "/api/generate" if spec["project_type"] == "web_app" else None

    if endpoint is not None:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        api_contract["endpoint"] = endpoint

    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
    if method is not None:
        method = method.strip().upper()
        if method not in allowed_methods:
            method = "POST"
    else:
        method = "POST" if spec["project_type"] == "web_app" else None
    if method is not None:
        api_contract["method"] = method

    if response is not None:
        api_contract["response"] = response

if api_contract:
    spec["api_contract"] = api_contract

runtime_rules = sanitize_runtime_rules(interpret.get("runtime_rules"))
if runtime_rules is not None:
    spec["runtime_rules"] = runtime_rules

quality_policy = {}
quality_profile = extract_structured(queue_text_lower, "quality_profile")
if quality_profile == "high_visual_game":
    quality_policy.update(
        {
            "mode": "quality_first",
            "focus": "visual_first",
            "simulation_level": "medium",
            "publish_gate": "strict",
            "auto_retry_on_fail": True,
            "max_retry_count": 1,
        }
    )

mode = as_enum_string(interpret.get("quality_mode"), {"quality_first", "speed_first", "balanced"})
if mode:
    quality_policy["mode"] = mode

focus = as_enum_string(interpret.get("quality_focus"), {"visual_first", "simulation_first", "balanced"})
if focus:
    quality_policy["focus"] = focus

sim_level = as_enum_string(interpret.get("simulation_level"), {"low", "medium", "high"})
if sim_level:
    quality_policy["simulation_level"] = sim_level

publish_gate = as_enum_string(interpret.get("publish_gate"), {"strict", "relaxed"})
if publish_gate:
    quality_policy["publish_gate"] = publish_gate

auto_retry_on_fail = as_bool(interpret.get("auto_retry_on_fail"))
if auto_retry_on_fail is not None:
    quality_policy["auto_retry_on_fail"] = auto_retry_on_fail

max_retry_count = as_int(interpret.get("max_retry_count"), min_value=0, max_value=3)
if max_retry_count is not None:
    quality_policy["max_retry_count"] = max_retry_count

if quality_policy:
    spec["quality_policy"] = quality_policy

requirements = parse_requirements(queue_text_raw)
if requirements:
    spec["requirements"] = requirements

# Open MD pipeline extension: expose IR-derived generic contracts while preserving legacy fields.
if isinstance(intent_ir, dict) and intent_ir:
    artifacts = intent_ir.get("artifact_targets")
    if isinstance(artifacts, list) and artifacts:
        spec["artifacts"] = [str(x) for x in artifacts if isinstance(x, str) and x]

    stack_hints = intent_ir.get("stack_hints")
    if isinstance(stack_hints, dict) and stack_hints:
        safe_hints = {}
        for k, v in stack_hints.items():
            if isinstance(k, str) and isinstance(v, (str, bool, int, float)):
                safe_hints[k] = v
        if safe_hints:
            spec["stack_hints"] = safe_hints

    feature_intents = intent_ir.get("feature_intents")
    if isinstance(feature_intents, list) and feature_intents:
        spec["capabilities"] = {"feature_intents": [str(x) for x in feature_intents if isinstance(x, str) and x]}

    ir_contracts = intent_ir.get("contracts")
    if isinstance(ir_contracts, dict):
        contracts = {}
        files = ir_contracts.get("files")
        if isinstance(files, list):
            files = [str(x) for x in files if isinstance(x, str) and x]
            if files:
                contracts["files"] = files

        behaviors = ir_contracts.get("behaviors")
        if isinstance(behaviors, list):
            behaviors = [str(x) for x in behaviors if isinstance(x, str) and x]
            if behaviors:
                contracts["behaviors"] = behaviors

        test_rules = ir_contracts.get("test_rules")
        if isinstance(test_rules, list):
            test_rules = [str(x) for x in test_rules if isinstance(x, str) and x]
            if test_rules:
                contracts["test_rules"] = test_rules

        if contracts:
            spec["contracts"] = contracts

    validation_plan = {}
    if spec.get("project_type") == "desktop_app":
        validation_plan["syntax_checks"] = ["python_compile"]
        validation_plan["test_commands"] = ["python3 -m pytest -k diff --maxfail=1"]
    elif spec.get("project_type") == "web_app":
        validation_plan["syntax_checks"] = ["json", "node_check"]
        validation_plan["test_commands"] = ["npm test"]
    if validation_plan:
        spec["validation_plan"] = validation_plan

    clarify_required = intent_ir.get("clarify_required")
    if isinstance(clarify_required, bool):
        spec["clarify_required"] = clarify_required

    ambiguities = intent_ir.get("ambiguities")
    if isinstance(ambiguities, list) and ambiguities:
        spec["ambiguities"] = [str(x) for x in ambiguities if isinstance(x, str) and x]


def check_type(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate(value, node, path):
    expected = node.get("type")
    if expected and not check_type(value, expected):
        raise ValueError(f"type error at {path}")

    allowed = node.get("enum")
    if allowed is not None and value not in allowed:
        raise ValueError(f"enum error at {path}")

    minimum = node.get("minimum")
    if minimum is not None and isinstance(value, (int, float)) and value < minimum:
        raise ValueError(f"minimum error at {path}")

    maximum = node.get("maximum")
    if maximum is not None and isinstance(value, (int, float)) and value > maximum:
        raise ValueError(f"maximum error at {path}")

    if expected == "object":
        props = node.get("properties", {})
        patterns = node.get("patternProperties", {})

        for key in node.get("required", []):
            if key not in value:
                raise ValueError(f"missing key at {path}: {key}")

        for key, child in props.items():
            if key in value:
                validate(value[key], child, f"{path}.{key}")

        for key, item in value.items():
            if key in props:
                continue

            matched = False
            for pattern, pat_schema in patterns.items():
                if re.match(pattern, key):
                    validate(item, pat_schema, f"{path}.{key}")
                    matched = True
                    break

            if not matched and node.get("additionalProperties") is False:
                raise ValueError(f"unexpected key at {path}: {key}")

    if expected == "array":
        item_schema = node.get("items")
        if item_schema is not None:
            for idx, item in enumerate(value):
                validate(item, item_schema, f"{path}[{idx}]")


validate(spec, schema, "spec")
print(json.dumps(spec, ensure_ascii=False, indent=2))

try:
    parser_log = "runtime/parser_intent.log"
    lines = []
    if os.path.exists(parser_log):
        with open(parser_log, "r", encoding="utf-8") as src:
            lines = src.readlines()

    lines.append(
        "source={source} web_score={web_score} desktop_score={desktop_score} infer_full_app={infer} "
        "signals=web:{web},desktop:{desktop},cli:{cli},voice:{voice},screen:{screen},generate:{gen},api:{api},frontend:{front},monitor:{monitor} "
        "neg=web:{nweb},frontend:{nfront},api:{napi}\n".format(
            source=source_queue or "none",
            web_score=web_score,
            desktop_score=desktop_score,
            infer=str(infer_full_app).lower(),
            web=web_hits,
            desktop=desktop_hits,
            cli=cli_hits,
            voice=voice_hits,
            screen=screen_input_hits,
            gen=generate_hits,
            api=api_hits,
            front=frontend_hits,
            monitor=monitor_hits,
            nweb=int(neg_web),
            nfront=int(neg_frontend),
            napi=int(neg_api),
        )
    )
    with open(parser_log, "w", encoding="utf-8") as dst:
        dst.writelines(lines[-200:])

    with open("runtime/index.log", "a", encoding="utf-8") as log:
        log.write(f"PARSER_SOURCE_QUEUE={source_queue or 'none'}\n")
        log.write(f"PARSER_INTENT_WEB_SCORE={web_score}\n")
        log.write(f"PARSER_INTENT_DESKTOP_SCORE={desktop_score}\n")
        log.write(
            "PARSER_INTENT_SIGNALS="
            f"web:{web_hits},desktop:{desktop_hits},cli:{cli_hits},voice:{voice_hits},screen:{screen_input_hits},generate:{generate_hits},api:{api_hits},frontend:{frontend_hits},monitor:{monitor_hits}\n"
        )
        log.write(
            "PARSER_INTENT_NEGATIONS="
            f"web:{int(neg_web)},frontend:{int(neg_frontend)},api:{int(neg_api)}\n"
        )
        log.write(f"PARSER_INTENT_INFER_FULL_APP={str(infer_full_app).lower()}\n")
except Exception:
    pass
PY
