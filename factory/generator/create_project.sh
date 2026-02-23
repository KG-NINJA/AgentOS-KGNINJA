#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

QUEUE_DIR="$ROOT/queue"
WORKSPACE_DIR="workspace"
TEMPLATE_DIR="factory/templates/base-project"
LOG_FILE="runtime/activity.log"

idea_file="$(find "$QUEUE_DIR" -maxdepth 1 -type f -name '*.md' | sort | head -n 1 || true)"
if [ -z "$idea_file" ]; then
  echo "create_project: no idea file found in $QUEUE_DIR" >&2
  exit 1
fi

highest=0
while IFS= read -r name; do
  n="${name#project-}"
  if [[ "$n" =~ ^[0-9]{3}$ ]]; then
    value=$((10#$n))
    if [ "$value" -gt "$highest" ]; then
      highest="$value"
    fi
  fi
done < <(find "$WORKSPACE_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null)

next=$((highest + 1))
project_id="project-$(printf '%03d' "$next")"
project_dir="$WORKSPACE_DIR/$project_id"

if [ -e "$project_dir" ]; then
  echo "create_project: target already exists: $project_dir" >&2
  exit 1
fi

cp -a "$TEMPLATE_DIR" "$project_dir"
mkdir -p "$project_dir/docs"
mv "$idea_file" "$project_dir/docs/IDEA.md"

printf '%s created %s from %s\n' "$(date -Is)" "$project_id" "$(basename "$idea_file")" >> "$LOG_FILE"

./factory/memory/record.sh "$project_id" "$(basename "$idea_file")"

echo "create_project: created $project_id"
