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

python3 - <<'PY' > runtime/entities.json
import json
import re

try:
    with open("runtime/interpret.json", "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {
        "project_type": "unknown",
        "ai_task": "unknown",
        "input_type": "unknown",
        "ui_type": "unknown",
    }

values = [
    str(data.get("project_type", "unknown")),
    str(data.get("ai_task", "unknown")),
    str(data.get("input_type", "unknown")),
    str(data.get("ui_type", "unknown")),
]

tokens = []
for value in values:
    norm = value.lower().replace("-", "_")
    tokens.extend([t for t in re.split(r"[_\s/]+", norm) if t and t != "unknown"])

verb_words = {
    "classify", "classification", "extract", "extraction",
    "generate", "generation", "summarize", "summarization", "qa"
}
modifier_words = {
    "auto", "autonomous", "multi", "mixed", "batch", "web", "cli", "api",
    "screen", "diff", "monitor", "markdown", "desktop", "gui", "capture", "pyside6"
}

noun = []
verb = []
modifier = []

for t in tokens:
    if t in verb_words:
        verb.append(t)
    elif t in modifier_words:
        modifier.append(t)
    else:
        noun.append(t)


def uniq(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

result = {
    "noun": uniq(noun),
    "verb": uniq(verb),
    "modifier": uniq(modifier),
}

print(json.dumps(result, ensure_ascii=False, indent=2))
PY
