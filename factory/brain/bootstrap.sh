#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

CONFIG_GET="$ROOT/factory/scripts/config_get.py"
structured_mode="false"
open_md_pipeline_enabled="false"
open_md_pipeline_shadow_mode="true"
if [ -x "$CONFIG_GET" ]; then
  structured_mode="$("$CONFIG_GET" structured_multi_agent false)"
  open_md_pipeline_enabled="$("$CONFIG_GET" open_md_pipeline_enabled false)"
  open_md_pipeline_shadow_mode="$("$CONFIG_GET" open_md_pipeline_shadow_mode true)"
fi
STRUCTURED_STATE="runtime/structured_state.json"
QUEUE_DIR="$ROOT/queue"

write_publish_blocked() {
  local project_dir="$1"
  local reason="$2"
  local project_id="unknown"
  local publish_path="apps/unknown/"
  if [ -n "$project_dir" ]; then
    project_id="$(basename "$project_dir")"
    publish_path="apps/${project_id}/"
  fi
  cat > runtime/publish.json <<EOF
{
  "project_id": "$project_id",
  "published": false,
  "url": "unknown",
  "branch": "gh-pages",
  "path": "$publish_path",
  "reason": "$reason"
}
EOF
}

if find "$QUEUE_DIR" -mindepth 1 -maxdepth 1 -type f 2>/dev/null | read -r _; then
  ./factory/memory/index.sh
  ./factory/parser/run.sh
  if [ "$open_md_pipeline_enabled" = "true" ]; then
    if [ -f runtime/intent_ir.json ]; then
      if ! ./factory/brain/clarify_gate.sh; then
        printf '%s BOOTSTRAP stop_at=clarify\n' "$(date -Is)" >> runtime/index.log
        exit 1
      fi
    fi
  elif [ "$open_md_pipeline_shadow_mode" = "true" ]; then
    if [ -f runtime/intent_ir.json ]; then
      ./factory/brain/clarify_gate.sh >/dev/null 2>&1 || true
      printf '%s BOOTSTRAP clarify_shadow=observed\n' "$(date -Is)" >> runtime/index.log
    fi
  fi
  if ! ./factory/brain/decision.sh; then
    printf '%s BOOTSTRAP stop_at=decision\n' "$(date -Is)" >> runtime/index.log
    exit 1
  fi
  if ! ./factory/nervous/reflex.sh; then
    printf '%s BOOTSTRAP stop_at=reflex\n' "$(date -Is)" >> runtime/index.log
    exit 1
  fi
  publish_dir=""
  if [ "$structured_mode" = "true" ]; then
    if [ ! -f "$STRUCTURED_STATE" ]; then
      echo "bootstrap: structured mode enabled but state missing" >&2
      exit 1
    fi
    publish_dir="$(
      python3 - "$STRUCTURED_STATE" "$ROOT" <<'PY'
import json
import os
import sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    state = json.load(fh)
project = state.get('project_dir', '')
if not project:
    print('')
else:
    if not os.path.isabs(project):
        project = os.path.join(sys.argv[2], project)
    print(project)
PY
    )"
    if [ -z "$publish_dir" ]; then
      echo "bootstrap: structured state missing project path" >&2
      exit 1
    fi
    echo "ROUTER_PROJECT_DIR=${publish_dir}"
  else
    router_output="$(./factory/generator/router.sh 2>&1)"
    router_status=$?
    printf '%s\n' "$router_output"
    if [ "$router_status" -ne 0 ]; then
      exit "$router_status"
    fi

    while IFS= read -r line; do
      case "$line" in
        ROUTER_PROJECT_DIR=*)
          publish_dir="${line#ROUTER_PROJECT_DIR=}"
          ;;
      esac
    done <<< "$router_output"
  fi

  if [ -x ./tools/post_generation_gate.sh ]; then
    if ! ./tools/post_generation_gate.sh; then
      write_publish_blocked "$publish_dir" "blocked_by_post_generation_gate"
      exit 1
    fi
  fi

  if [ -f runtime/spec.json ] && [ -x ./tools/quality_gate_game.sh ]; then
    publish_gate="$(jq -r '.quality_policy.publish_gate // "relaxed"' runtime/spec.json 2>/dev/null || echo "relaxed")"
    auto_retry="$(jq -r '.quality_policy.auto_retry_on_fail // false' runtime/spec.json 2>/dev/null || echo "false")"
    max_retry="$(jq -r '.quality_policy.max_retry_count // 0' runtime/spec.json 2>/dev/null || echo "0")"

    retries=0
    while true; do
      if ./tools/quality_gate_game.sh; then
        break
      fi

      if [ "$publish_gate" != "strict" ]; then
        echo "QUALITY_GATE: failed but publish_gate=relaxed, continuing"
        break
      fi

      if [ "$auto_retry" != "true" ] || [ "$retries" -ge "$max_retry" ]; then
        echo "QUALITY_GATE: strict gate failed, stopping publish"
        write_publish_blocked "$publish_dir" "blocked_by_quality_gate"
        exit 1
      fi

      retries=$((retries + 1))
      echo "QUALITY_GATE: retry $retries/$max_retry"
      ./factory/generator/router.sh
    done
  fi

  if [ -n "$publish_dir" ]; then
    ./factory/publish/publish.sh "$publish_dir"
  fi

  if [ -x ./tools/feedback_pack.sh ]; then
    ./tools/feedback_pack.sh
  elif [ -x ./tools/analyze_logs.sh ]; then
    ./tools/analyze_logs.sh
  fi
else
  echo "[BOOTSTRAP] No queue items found"
  exit 0
fi
