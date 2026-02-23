#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="reflector"
ROLE_INPUT="${1:-}"
ROLE_OUTPUT="${2:-}"
if [ $# -ne 2 ]; then
  echo "reflector: usage reflector.sh <critique_report> <memory_log>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib.sh"
setup_role_trap

ensure_file_exists "$ROLE_INPUT" "reflector critique"
mkdir -p "$(dirname "$ROLE_OUTPUT")"

python3 - "$ROLE_INPUT" "$ROLE_OUTPUT" "$STRUCTURED_LOG_FILE" <<'PY'
import datetime as dt
import sys
from pathlib import Path

critique = Path(sys.argv[1])
memory_log = Path(sys.argv[2])
structured_log = Path(sys.argv[3])
critique_text = critique.read_text(encoding='utf-8')
log_tail = ''
if structured_log.exists():
    log_tail = '\n'.join(structured_log.read_text(encoding='utf-8').splitlines()[-5:])

today = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')
entry_lines = [f'## {today} Structured Reflection']
entry_lines.append('### Outcome Summary')
entry_lines.append(critique_text.splitlines()[0] if critique_text.splitlines() else 'No critique content')
entry_lines.append('')
entry_lines.append('### Key Takeaways')
for line in critique_text.splitlines():
    if line.startswith('- '):
        entry_lines.append(line)
if log_tail:
    entry_lines.append('')
    entry_lines.append('### Execution Trace Highlights')
    entry_lines.append(log_tail)
entry_lines.append('')
entry = '\n'.join(entry_lines)
with memory_log.open('a', encoding='utf-8') as fh:
    fh.write(entry + '\n')
PY

append_guardrail_question "$ROLE_OUTPUT"
append_line_with_check "$ROLE_OUTPUT" "NEXT_CHANGE: Inspect docs/ARCHITECTURE.md to validate plan"

role_success "memory updated"
