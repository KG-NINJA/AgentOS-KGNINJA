#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/user/kg-autonomous}"
REPO_DIR="${REPO_DIR:-$WORKDIR}"
BRANCH="${BRANCH:-main}"
COMMIT_PREFIX="${COMMIT_PREFIX:-Factory}"
# Keep default narrow to generated deliverables.
PATHS="${PATHS:-workspace}"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

cd "$REPO_DIR"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "[$(timestamp)] ERROR: not a git repo: $REPO_DIR"
  exit 2
}

git add -A $PATHS
if git diff --cached --quiet; then
  echo "[$(timestamp)] nothing to commit"
  exit 0
fi

msg="${COMMIT_PREFIX}: auto update $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
git commit -m "$msg"
git push origin "$BRANCH"
echo "[$(timestamp)] pushed to $BRANCH"
