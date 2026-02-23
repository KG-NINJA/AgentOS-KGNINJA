#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

branch="gh-pages"
worktree_dir="runtime/publish-gh-pages"
publish_root_rel="apps"
mode="${FACTORY_PUBLISH_MODE:-git}"

project_dir="${1:-}"
if [ -z "$project_dir" ]; then
  project_dir=""
fi

write_status() {
  local published="$1"
  local url="$2"
  local reason="${3:-}"
  local reason_block=""
  local escaped_reason=""
  if [ -n "$reason" ]; then
    escaped_reason="${reason//\"/\\\"}"
    escaped_reason="${escaped_reason//$'\n'/ }"
    reason_block=",\n  \"reason\": \"${escaped_reason}\""
  fi
  printf '{\n  "project_id": "%s",\n  "published": %s,\n  "url": "%s",\n  "branch": "%s",\n  "path": "%s"%b\n}\n' \
    "$project_id" "$published" "$url" "$branch" "$publish_path_json" "$reason_block" > runtime/publish.json
}

fail_publish() {
  write_status false "unknown" "$1"
  exit 0
}

if [ -z "$project_dir" ]; then
  project_id="unknown"
  publish_path_json="apps/unknown/"
  fail_publish "missing project directory argument"
fi

if [ ! -d "$project_dir" ]; then
  project_id="$(basename "$project_dir")"
  publish_path_json="apps/${project_id}/"
  fail_publish "project directory not found"
fi

project_id="$(basename "$project_dir")"
publish_path="${publish_root_rel}/${project_id}"
publish_path_json="${publish_path}/"

if [ "$mode" = "local" ]; then
  local_root="runtime/published"
  local_target="${local_root}/${project_id}"
  rm -rf "$local_target"
  mkdir -p "$local_root"
  if ! cp -a "$project_dir" "$local_target"; then
    fail_publish "failed to copy project into local publish directory"
  fi
  write_status true "$local_target" "local publish mode"
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  fail_publish "git command not available"
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  fail_publish "repository not initialized"
fi

mkdir -p runtime

if [ -d "$worktree_dir" ]; then
  rm -rf "$worktree_dir"
fi

if ! git worktree add -B "$branch" "$worktree_dir" HEAD >/dev/null 2>&1; then
  fail_publish "unable to create gh-pages worktree"
fi

publish_target="$worktree_dir/$publish_path"
rm -rf "$publish_target"
mkdir -p "$(dirname "$publish_target")"

if ! cp -a "$project_dir" "$publish_target"; then
  fail_publish "failed to copy project into publish worktree"
fi

if ! git -C "$worktree_dir" add "$publish_path" >/dev/null 2>&1; then
  fail_publish "git add failed"
fi

if git -C "$worktree_dir" diff --cached --quiet; then
  write_status false "unknown" "no changes to publish"
  exit 0
fi

if ! git -C "$worktree_dir" commit -m "publish: $project_id" >/dev/null 2>&1; then
  fail_publish "git commit failed"
fi

remote_url="unknown"
if git remote get-url origin >/dev/null 2>&1; then
  remote_url="$(git remote get-url origin 2>/dev/null || echo "unknown")"
fi

url="unknown"
if [ "$remote_url" != "unknown" ]; then
  stripped="$remote_url"
  stripped="${stripped%.git}"
  if [[ "$stripped" =~ github.com[:/]+([^/]+)/([^/]+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    url="https://${owner}.github.io/${repo}/apps/${project_id}/"
  fi
fi

push_failed=0
if git remote get-url origin >/dev/null 2>&1; then
  if ! git -C "$worktree_dir" push origin "$branch" >/dev/null 2>&1; then
    push_failed=1
  fi
fi

if [ "$push_failed" -eq 1 ]; then
  write_status false "$url" "git push failed"
  exit 0
fi

write_status true "$url"
exit 0
