#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SPEC_FILE="runtime/spec.json"
SKILLS_FILE="factory/skills.json"
QUEUE_DIR="$ROOT/queue"

if [ ! -f "$SPEC_FILE" ]; then
  exit 0
fi

echo "ROUTER: reading spec"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

if [ ! -f "$SKILLS_FILE" ]; then
  exit 0
fi

project_type="$(jq -r '.project_type // ""' "$SPEC_FILE" 2>/dev/null || true)"
project_type="$(printf '%s' "$project_type" | tr '[:upper:]' '[:lower:]')"

ui_type="$(jq -r '.ui_type // ""' "$SPEC_FILE" 2>/dev/null || true)"
ui_type="$(printf '%s' "$ui_type" | tr '[:upper:]' '[:lower:]')"

ai_task="$(jq -r '.ai_task // ""' "$SPEC_FILE" 2>/dev/null || true)"
ai_task="$(printf '%s' "$ai_task" | tr '[:upper:]' '[:lower:]')"
artifacts="$(jq -r '.artifacts // [] | join(" ")' "$SPEC_FILE" 2>/dev/null || true)"
artifacts="$(printf '%s' "$artifacts" | tr '[:upper:]' '[:lower:]')"
feature_intents="$(jq -r '.capabilities.feature_intents // [] | join(" ")' "$SPEC_FILE" 2>/dev/null || true)"
feature_intents="$(printf '%s' "$feature_intents" | tr '[:upper:]' '[:lower:]')"

echo "ROUTER: project_type=$project_type"
echo "ROUTER: ui_type=$ui_type"
echo "ROUTER: ai_task=$ai_task"
echo "ROUTER: artifacts=$artifacts"
echo "ROUTER: feature_intents=$feature_intents"

get_highest_project_number() {
  local highest=0
  shopt -s nullglob
  for dir in workspace/project-*; do
    [ -d "$dir" ] || continue
    local name="${dir##*/}"
    local number="${name#project-}"
    if [[ "$number" =~ ^[0-9]{3}$ ]]; then
      local value=$((10#$number))
      if [ "$value" -gt "$highest" ]; then
        highest="$value"
      fi
    fi
  done
  shopt -u nullglob
  printf '%s' "$highest"
}

before_highest="$(get_highest_project_number)"

mapfile -t skills < <(jq -c '.skills[]' "$SKILLS_FILE")

selected_script=""
default_script=""
best_score=-999999

for skill in "${skills[@]}"; do
  script="$(jq -r '.script // ""' <<<"$skill")"
  mapfile -t keywords < <(jq -r '.match[]?' <<<"$skill")
  mapfile -t when_caps < <(jq -r '.when.capabilities[]?' <<<"$skill")
  mapfile -t when_stack < <(jq -r '.when.stack[]?' <<<"$skill")
  priority="$(jq -r '.priority // 0' <<<"$skill" 2>/dev/null || echo "0")"
  [ -n "$priority" ] || priority=0

  skill_has_default=0
  candidate_hit=0
  score=0

  for cap in "${when_caps[@]}"; do
    cap="$(printf '%s' "$cap" | tr '[:upper:]' '[:lower:]')"
    if [[ "$feature_intents" == *"$cap"* ]] || [[ "$artifacts" == *"$cap"* ]]; then
      candidate_hit=1
      score=$((score + 2))
    fi
  done

  for stack_token in "${when_stack[@]}"; do
    stack_token="$(printf '%s' "$stack_token" | tr '[:upper:]' '[:lower:]')"
    if [[ "$project_type" == *"$stack_token"* ]] || [[ "$ui_type" == *"$stack_token"* ]] || [[ "$ai_task" == *"$stack_token"* ]] || [[ "$artifacts" == *"$stack_token"* ]]; then
      candidate_hit=1
      score=$((score + 1))
    fi
  done

  for keyword in "${keywords[@]}"; do
    keyword="$(printf '%s' "$keyword" | tr '[:upper:]' '[:lower:]')"

    if [ "$keyword" = "default" ]; then
      skill_has_default=1
      continue
    fi

    if [[ "$project_type" == *"$keyword"* ]] || [[ "$ui_type" == *"$keyword"* ]] || [[ "$ai_task" == *"$keyword"* ]] || [[ "$artifacts" == *"$keyword"* ]] || [[ "$feature_intents" == *"$keyword"* ]]; then
      candidate_hit=1
      score=$((score + 2))
    fi
  done

  if [ "$candidate_hit" -eq 1 ]; then
    score=$((score + priority))
    if [ -z "$selected_script" ] || [ "$score" -gt "$best_score" ]; then
      selected_script="$script"
      best_score="$score"
    fi
  fi

  if [ "$skill_has_default" -eq 1 ] && [ -z "$default_script" ]; then
    default_script="$script"
  fi
done

if [ -z "$selected_script" ]; then
  selected_script="$default_script"
fi

if [ -z "$selected_script" ]; then
  exit 0
fi

echo "ROUTER: skill=$selected_script"

if [ "$selected_script" = "./factory/generator/create_project.sh" ]; then
  if [ ! -d "$QUEUE_DIR" ]; then
    exit 0
  fi
  shopt -s nullglob
  queue_files=("$QUEUE_DIR"/*.md)
  shopt -u nullglob
  if [ "${#queue_files[@]}" -eq 0 ]; then
    exit 0
  fi
fi

"$selected_script"

after_highest="$(get_highest_project_number)"

if [ -n "$after_highest" ] && [ "$after_highest" -gt "${before_highest:-0}" ]; then
  new_dir="workspace/project-$(printf '%03d' "$after_highest")"
  if [ -d "$new_dir" ]; then
    echo "ROUTER_PROJECT_DIR=$new_dir"
  fi
fi
