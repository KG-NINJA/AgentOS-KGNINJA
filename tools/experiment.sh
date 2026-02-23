#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
QUEUE_DIR="$ROOT/queue"

log() {
  echo "[EXPERIMENT] $*"
}

count_projects() {
  find workspace -mindepth 1 -maxdepth 1 -type d -name 'project-*' | wc -l | tr -d ' '
}

latest_project_dir() {
  find workspace -mindepth 1 -maxdepth 1 -type d -name 'project-*' | sort | tail -n 1
}

validate_skills_registry() {
  python3 - <<'PY'
import json

with open("factory/skills.json", "r", encoding="utf-8") as f:
    data = json.load(f)

if not isinstance(data, dict):
    raise SystemExit(1)
skills = data.get("skills")
if not isinstance(skills, list):
    raise SystemExit(1)

for skill in skills:
    if not isinstance(skill, dict):
        raise SystemExit(1)
    match = skill.get("match")
    script = skill.get("script")
    if not isinstance(match, list) or not match:
        raise SystemExit(1)
    if not all(isinstance(x, str) and x for x in match):
        raise SystemExit(1)
    if not isinstance(script, str) or not script:
        raise SystemExit(1)
PY
}

heal_skills_registry() {
  python3 - <<'PY'
import json
from json import JSONDecoder

path = "factory/skills.json"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

decoder = JSONDecoder()
obj, _ = decoder.raw_decode(text.lstrip())

if not isinstance(obj, dict):
    raise SystemExit(1)
skills = obj.get("skills")
if not isinstance(skills, list):
    raise SystemExit(1)

for skill in skills:
    if not isinstance(skill, dict):
        raise SystemExit(1)
    match = skill.get("match")
    script = skill.get("script")
    if not isinstance(match, list) or not match:
        raise SystemExit(1)
    if not all(isinstance(x, str) and x for x in match):
        raise SystemExit(1)
    if not isinstance(script, str) or not script:
        raise SystemExit(1)

with open(path, "w", encoding="utf-8") as f:
    json.dump(obj, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

precheck_skills_registry() {
  if validate_skills_registry >/dev/null 2>&1; then
    return 0
  fi

  log "skills.json invalid, attempting auto-heal"
  if heal_skills_registry >/dev/null 2>&1 && validate_skills_registry >/dev/null 2>&1; then
    log "skills.json healed"
    return 0
  fi

  log "skills.json heal failed"
  exit 1
}

STAMP="$(date +%Y%m%d-%H%M%S)"
IDEA_FILE="$QUEUE_DIR/exp-${STAMP}.md"
BACKUP_SPEC="runtime/spec.json.bak.${STAMP}"
RESTORE_NEEDED=0

cleanup() {
  if [ "$RESTORE_NEEDED" -eq 1 ] && [ -f "$BACKUP_SPEC" ]; then
    cp -a "$BACKUP_SPEC" runtime/spec.json
    rm -f "$BACKUP_SPEC"
  fi
}
trap cleanup EXIT

log "precheck: verify + tools"
./tools/verify.sh
command -v codex >/dev/null
precheck_skills_registry
./factory.sh status

log "phase1: parser think mode"
mkdir -p "$QUEUE_DIR"
cat > "$IDEA_FILE" <<EOF
Build a web_app for music_generation from voice_input with web_interface. run_id=${STAMP}
EOF
./factory.sh think
cat runtime/interpret.json
cat runtime/spec.json

log "phase2: end-to-end run"
before_count="$(count_projects)"
./factory.sh run
after_count="$(count_projects)"
latest="$(latest_project_dir)"
echo "before_projects=$before_count"
echo "after_projects=$after_count"
echo "latest_project=$latest"

if [ -z "$latest" ]; then
  echo "[EXPERIMENT] latest project not found" >&2
  exit 1
fi

test -f "$latest/core/app.js"
test -f "$latest/docs/ARCHITECTURE.md"
test -d "$latest/tests"
log "phase2: scaffold assertions passed"

log "phase3: router force test"
if [ -f runtime/spec.json ]; then
  cp -a runtime/spec.json "$BACKUP_SPEC"
  RESTORE_NEEDED=1
fi
cat > runtime/spec.json <<'EOF'
{
  "project_type": "web_app",
  "ai_task": "music_generation",
  "input_type": "voice_input",
  "ui_type": "web_interface",
  "entities": {}
}
EOF
bash factory/generator/router.sh
cp -a "$BACKUP_SPEC" runtime/spec.json
rm -f "$BACKUP_SPEC"
RESTORE_NEEDED=0

log "phase4: reliability loop"
for i in 1 2 3; do
  cat > "$QUEUE_DIR/exp-loop-${STAMP}-${i}.md" <<EOF
web_app music_generation voice_input web_interface loop=${i} stamp=${STAMP}
EOF
  ./factory.sh run
done
./factory.sh status

log "done"
