#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="planner"
ROLE_INPUT="${1:-}"
ROLE_OUTPUT="${2:-}"
if [ $# -ne 2 ]; then
  echo "planner: usage planner.sh <input_markdown> <output_yaml>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=factory/roles/lib.sh
. "$SCRIPT_DIR/lib.sh"
setup_role_trap

ensure_file_exists "$ROLE_INPUT" "planner input"
ensure_parent_dir "$ROLE_OUTPUT"

tmp_file="$(mktemp)"
SOURCE_ROOT="${STRUCTURED_QUEUE_ROOT:-$ROOT}"

python3 - "$ROLE_INPUT" "$tmp_file" "$SOURCE_ROOT" <<'PY'
import html
import json
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
source_root = Path(sys.argv[3]).resolve()
text = src.read_text(encoding="utf-8").strip()
if not text:
    raise SystemExit("planner: input markdown empty")
lines = text.splitlines()

def first_heading():
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            return stripped.lstrip('#').strip()
    return src.stem.replace('_', ' ').title()

def extract_requirements():
    reqs = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in {'-', '*', '•'}:
            value = stripped.lstrip('-*•').strip()
            if value:
                reqs.append(value)
    if not reqs:
        reqs = [
            "Maintain deterministic builder steps",
            "Keep scaffold minimal and auditable",
        ]
    return reqs

data = {
    "title": first_heading(),
    "source_file": "",
    "problem_statement": text,
    "requirements": extract_requirements(),
}
try:
    relative = src.relative_to(source_root)
    data["source_file"] = relative.as_posix()
except ValueError:
    data["source_file"] = src.name

def yaml_escape(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'

with out.open('w', encoding='utf-8') as fh:
    fh.write(f'title: {yaml_escape(data["title"])}\n')
    fh.write(f'source_file: {yaml_escape(data["source_file"])}\n')
    fh.write('problem_statement: |\n')
    if data["problem_statement"]:
        for line in data["problem_statement"].splitlines():
            fh.write(f'  {line}\n')
    else:
        fh.write('  \n')
    fh.write('requirements:\n')
    for req in data["requirements"]:
        fh.write(f'  - {yaml_escape(req)}\n')
PY

mv "$tmp_file" "$ROLE_OUTPUT"
append_guardrail_question "$ROLE_OUTPUT"
role_success "intent spec generated"
