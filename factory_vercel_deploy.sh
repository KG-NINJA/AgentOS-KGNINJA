#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${TARGET_DIR:-/home/user/kg-autonomous/workspace/latest}"
LOG_PATH="${LOG_PATH:-/home/user/kg-autonomous/runtime/vercel_deploy.log}"
VERCEL_TOKEN="${VERCEL_TOKEN:-}"
VERCEL_ARGS="${VERCEL_ARGS:-}"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

if [ -z "$VERCEL_TOKEN" ]; then
  echo "[$(timestamp)] ERROR: VERCEL_TOKEN is empty" | tee -a "$LOG_PATH"
  exit 2
fi

if [ ! -d "$TARGET_DIR" ]; then
  echo "[$(timestamp)] ERROR: TARGET_DIR not found: $TARGET_DIR" | tee -a "$LOG_PATH"
  exit 2
fi

cd "$TARGET_DIR"
if [ ! -d ".vercel" ]; then
  echo "[$(timestamp)] ERROR: .vercel not found in $TARGET_DIR (run 'vercel link' once)" | tee -a "$LOG_PATH"
  exit 2
fi

echo "[$(timestamp)] deploying TARGET_DIR=$TARGET_DIR" | tee -a "$LOG_PATH"
out="$(mktemp)"
vercel deploy $VERCEL_ARGS --token "$VERCEL_TOKEN" 2>&1 | tee "$out" | tee -a "$LOG_PATH"
url="$(grep -Eo 'https://[a-zA-Z0-9._-]+\\.vercel\\.app/?' "$out" | tail -n 1 || true)"
rm -f "$out"

if [ -n "$url" ]; then
  echo "[$(timestamp)] DEPLOY_URL=$url" | tee -a "$LOG_PATH"
  echo "$url"
else
  echo "[$(timestamp)] WARN: deploy url not found in output" | tee -a "$LOG_PATH"
fi
