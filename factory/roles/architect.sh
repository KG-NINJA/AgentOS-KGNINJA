#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="architect"
ROLE_INPUT="${1:-}"
ROLE_OUTPUT="${2:-}"
if [ $# -ne 2 ]; then
  echo "architect: usage architect.sh <intent_yaml> <architecture_md>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib.sh"
setup_role_trap

ensure_file_exists "$ROLE_INPUT" "architect input"
ensure_parent_dir "$ROLE_OUTPUT"

python3 - "$ROLE_INPUT" "$ROLE_OUTPUT" "$ROOT" <<'PY'
import sys
from pathlib import Path

intent_path = Path(sys.argv[1])
arch_path = Path(sys.argv[2])
root_dir = Path(sys.argv[3]).resolve()

lines = intent_path.read_text(encoding="utf-8").splitlines()

title = "Untitled"
source_file = "unknown"
requirements = []
problem: list[str] = []
mode = None
for raw in lines:
    line = raw.rstrip('\n')
    if line.startswith('title:'):
        title = line.split(':', 1)[1].strip().strip('"')
        continue
    if line.startswith('source_file:'):
        source_file = line.split(':', 1)[1].strip().strip('"')
        continue
    if line.startswith('problem_statement:'):
        mode = 'problem'
        continue
    if line.startswith('requirements:'):
        mode = 'requirements'
        continue
    if mode == 'problem':
        if line.startswith('  '):
            problem.append(line[2:])
            continue
        else:
            mode = None
    if mode == 'requirements':
        if line.strip().startswith('-'):
            req = line.split('-', 1)[1].strip().strip('"')
            if req:
                requirements.append(req)
            continue
        else:
            mode = None

if not requirements:
    requirements = ["Keep scaffold minimal"]

summary = '\n'.join(problem).strip()
if not summary:
    summary = "No detailed problem statement supplied."

intent_display = str(intent_path)
try:
    intent_display = str(intent_path.resolve().relative_to(root_dir))
except ValueError:
    intent_display = intent_path.name

base_dirs = ["core/", "core/public/", "docs/", "tests/"]
extra_dirs = []
for req in requirements:
    lowered = req.lower()
    if "api" in lowered and "core/api/" not in extra_dirs:
        extra_dirs.append("core/api/")
    if "data" in lowered and "data/" not in extra_dirs:
        extra_dirs.append("data/")

dirs = base_dirs + extra_dirs
file_blueprint = [
    ("core/main.py", "Execution entry coordinating modules"),
    ("core/public/index.html", "User-facing shell"),
    ("docs/ARCHITECTURE.md", "Architecture reference"),
    ("tests/test_smoke.py", "Minimal regression guard"),
]
if "api" in ' '.join(requirements).lower():
    file_blueprint.append(("core/api/routes.py", "HTTP interface skeleton"))

with arch_path.open('w', encoding='utf-8') as fh:
    fh.write('# Architecture Plan\n\n')
    fh.write(f'Project: {title}\n')
    fh.write(f'SourceQueue: {source_file}\n')
    fh.write(f'IntentSpec: {intent_display}\n\n')
    fh.write('## IntentSummary\n')
    fh.write(f'{summary}\n\n')
    fh.write('## DirectoryLayout\n')
    for directory in dirs:
        fh.write(f'- {directory}\n')
    fh.write('\n## FileBlueprint\n')
    for path, desc in file_blueprint:
        fh.write(f'- {path} | {desc}\n')
    fh.write('\n## Dependencies\n')
    deps = ["python >=3.10", "node >=18"]
    for dep in deps:
        fh.write(f'- {dep}\n')
    fh.write('\n## RequirementsEcho\n')
    for req in requirements:
        fh.write(f'- {req}\n')
PY

role_success "architecture drafted"
append_guardrail_question "$ROLE_OUTPUT"
