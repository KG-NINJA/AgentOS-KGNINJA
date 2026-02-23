#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="builder"
ROLE_INPUT="${1:-}"
ROLE_OUTPUT="${2:-}"
if [ $# -ne 2 ]; then
  echo "builder: usage builder.sh <architecture_md> <target_project_dir>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib.sh"
setup_role_trap

ensure_file_exists "$ROLE_INPUT" "builder architecture"
if [ -e "$ROLE_OUTPUT" ]; then
  echo "builder: target already exists: $ROLE_OUTPUT" >&2
  exit 1
fi

mkdir -p "$(dirname "$ROLE_OUTPUT")"

queue_root="${STRUCTURED_QUEUE_ROOT:-$ROOT}"

python3 - "$ROLE_INPUT" "$ROLE_OUTPUT" "$ROOT" "$queue_root" <<'PY'
import os
import shutil
import sys
from pathlib import Path

arch_path = Path(sys.argv[1])
project_dir = Path(sys.argv[2])
root_dir = Path(sys.argv[3])
queue_root = Path(sys.argv[4])

sections: dict[str, list[str]] = {
    "DirectoryLayout": [],
    "FileBlueprint": [],
    "Dependencies": [],
    "RequirementsEcho": [],
}
project_title = "Untitled"
source_queue_text = ""
current_section = None

for raw in arch_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith('Project:'):
        project_title = line.split(':', 1)[1].strip()
        continue
    if line.startswith('SourceQueue:'):
        source_queue_text = line.split(':', 1)[1].strip()
        continue
    if line.startswith('## '):
        title = line[3:]
        if title in sections:
            current_section = title
        else:
            current_section = None
        continue
    if current_section and line.startswith('- '):
        sections[current_section].append(line[2:])

if not sections["DirectoryLayout"]:
    raise SystemExit("builder: missing directory plan")

project_dir.mkdir(parents=True, exist_ok=False)

for rel_dir in sections["DirectoryLayout"]:
    clean = rel_dir.strip()
    if not clean:
        continue
    clean = clean.replace('..', '')
    clean = clean.lstrip('/')
    target = project_dir / clean
    target.mkdir(parents=True, exist_ok=True)

for blueprint in sections["FileBlueprint"]:
    parts = [p.strip() for p in blueprint.split('|', 1)]
    path = parts[0]
    desc = parts[1] if len(parts) > 1 else "Generic placeholder"
    safe = path.replace('..', '').lstrip('/')
    file_path = project_dir / safe
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = []
    content.append(f"# {project_title}")
    content.append(f"# Role: {desc}")
    content.append("")
    content.append("This scaffold file was generated deterministically by the structured builder.")
    file_path.write_text('\n'.join(content), encoding='utf-8')

arch_copy = project_dir / 'docs/ARCHITECTURE.md'
arch_copy.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(arch_path, arch_copy)

queue_basename = ""
source_path = None
if source_queue_text:
    candidate = Path(source_queue_text)
    if not candidate.is_absolute():
        candidate = (queue_root / candidate).resolve()
    if candidate.exists():
        source_path = candidate
        queue_basename = candidate.name
        target = project_dir / 'docs/IDEA.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target)

metadata = {
    "project_id": project_dir.name,
    "queue_basename": queue_basename,
}
(metadata_path := project_dir / '.structured_builder.json').write_text(str(metadata), encoding='utf-8')
PY

guardrail_file="$ROLE_OUTPUT/STRUCTURED_GUARDRAILS.txt"
append_guardrail_question "$guardrail_file"

meta_file="$ROLE_OUTPUT/.structured_builder.json"
idea_name="$(basename "$ROLE_OUTPUT")"
if [ -f "$meta_file" ]; then
  idea_name="$(python3 - "$meta_file" "$ROLE_OUTPUT" <<'PY'
import ast
import sys
from pathlib import Path
meta = Path(sys.argv[1])
info = ast.literal_eval(meta.read_text(encoding='utf-8'))
fallback = Path(sys.argv[2]).name
print(info.get('queue_basename') or fallback)
PY
)"
fi

if [ "${STRUCTURED_SKIP_MEMORY_RECORD:-0}" != "1" ]; then
  if [ -x "$ROOT/factory/memory/record.sh" ]; then
    "$ROOT/factory/memory/record.sh" "$(basename "$ROLE_OUTPUT")" "$idea_name"
  fi
fi

role_success "project scaffold created"
