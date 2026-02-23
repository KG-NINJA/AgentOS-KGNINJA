#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="critic"
ROLE_INPUT="${1:-}"
ROLE_OUTPUT="${2:-}"
if [ $# -ne 2 ]; then
  echo "critic: usage critic.sh <project_dir> <critique_report>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib.sh"
setup_role_trap

if [ ! -d "$ROLE_INPUT" ]; then
  echo "critic: project dir missing: $ROLE_INPUT" >&2
  exit 1
fi
ensure_parent_dir "$ROLE_OUTPUT"

python3 - "$ROLE_INPUT" "$ROLE_OUTPUT" <<'PY'
import json
import sys
from pathlib import Path

project = Path(sys.argv[1])
report_path = Path(sys.argv[2])
issues: list[str] = []
suggestions: list[str] = []

arch = project / 'docs/ARCHITECTURE.md'
if not arch.exists():
    issues.append('Architecture reference missing (docs/ARCHITECTURE.md).')
else:
    planned_dirs = []
    planned_files = []
    section = None
    for line in arch.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            continue
        if section == 'DirectoryLayout' and line.strip().startswith('- '):
            planned_dirs.append(line.split('- ', 1)[1].strip())
        if section == 'FileBlueprint' and line.strip().startswith('- '):
            planned_files.append(line.split('- ', 1)[1].strip())
    for rel in planned_dirs:
        rel = rel.replace('..', '').lstrip('/')
        if not (project / rel).exists():
            issues.append(f'Missing directory from plan: {rel}')
    for fp in planned_files:
        path = fp.split('|', 1)[0].strip().replace('..', '').lstrip('/')
        target = project / path
        if not target.exists():
            issues.append(f'Missing file from blueprint: {path}')
        else:
            if not target.read_text(encoding='utf-8').strip():
                suggestions.append(f'Fill placeholder content in {path}.')

files_in_project = sorted(p.relative_to(project).as_posix() for p in project.rglob('*') if p.is_file())
if len(files_in_project) > 50:
    issues.append('Project may be overengineered (>50 files).')

if not issues:
    suggestions.append('Structure matches plan; proceed to quality gate.')

lines = ['# Critique Report', '']
lines.append(f'Project: {project.name}')
lines.append('')
lines.append('## Issues')
if issues:
    for item in issues:
        lines.append(f'- {item}')
else:
    lines.append('- None detected')
lines.append('')
lines.append('## Suggestions')
if suggestions:
    for item in suggestions:
        lines.append(f'- {item}')
else:
    lines.append('- Provide targeted improvements next iteration')

report_path.write_text('\n'.join(lines), encoding='utf-8')
PY

append_guardrail_question "$ROLE_OUTPUT"
append_line_with_check "$ROLE_OUTPUT" "MOST_FRAGILE_POINT: docs/ARCHITECTURE.md is an unverified dependency"

role_success "critique completed"
