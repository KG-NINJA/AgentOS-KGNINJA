#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/user/kg-autonomous}"
QUEUE_DIR="${QUEUE_DIR:-$WORKDIR/queue}"
LOGDIR="${LOGDIR:-$WORKDIR/runtime/factory_os}"
LOCKFILE="${LOCKFILE:-$LOGDIR/lock_publish}"
POLL_SEC="${POLL_SEC:-10}"
DEPLOY_TARGET="${DEPLOY_TARGET:-pages}"

mkdir -p "$LOGDIR" "$QUEUE_DIR"
touch "$LOGDIR/seen_publish.txt"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "[$(timestamp)] already running (lock busy). exit."
  exit 0
fi

publish_pages_once() {
  if [ -f "$WORKDIR/runtime/publish.json" ]; then
    local pub status url reason
    pub="$(python3 - <<'PY' "$WORKDIR/runtime/publish.json"
import json,sys
p=sys.argv[1]
obj=json.load(open(p,encoding='utf-8'))
print(str(obj.get("published", False)).lower())
print(obj.get("url",""))
print(obj.get("reason",""))
PY
)"
    status="$(printf '%s\n' "$pub" | sed -n '1p')"
    url="$(printf '%s\n' "$pub" | sed -n '2p')"
    reason="$(printf '%s\n' "$pub" | sed -n '3p')"
    echo "[$(timestamp)] publish_status=$status url=$url reason=$reason"
  else
    echo "[$(timestamp)] publish_status=unknown reason=missing_runtime_publish_json"
  fi
}

run_once() {
  local run_id log rc
  run_id="$(date -u +"%Y%m%d_%H%M%S")"
  log="$LOGDIR/run_publish_${run_id}.log"
  echo "[$(timestamp)] trigger run_id=$run_id deploy_target=$DEPLOY_TARGET" | tee -a "$log"

  (
    cd "$WORKDIR"
    bash ./factory.sh run
    bash ./factory_git_push.sh

    if [ "$DEPLOY_TARGET" = "pages" ] || [ "$DEPLOY_TARGET" = "both" ]; then
      publish_pages_once
    fi

    if [ "$DEPLOY_TARGET" = "vercel" ] || [ "$DEPLOY_TARGET" = "both" ]; then
      if [ -x ./factory_vercel_deploy.sh ]; then
        bash ./factory_vercel_deploy.sh || true
      else
        echo "[$(timestamp)] vercel skipped: factory_vercel_deploy.sh not found"
      fi
    fi
  ) >>"$log" 2>&1
  rc=$?

  echo "[$(timestamp)] done rc=$rc" | tee -a "$log"
  return "$rc"
}

list_queue_files() { find "$QUEUE_DIR" -maxdepth 1 -type f -name '*.md' 2>/dev/null | sort || true; }
mark_seen() { cat > "$LOGDIR/seen_publish.txt"; }
diff_new_files() {
  local current_file="$LOGDIR/current_publish.txt"
  cat > "$current_file"
  comm -13 "$LOGDIR/seen_publish.txt" "$current_file" || true
}
handle_new_files() {
  local n
  n="$(wc -l | tr -d ' ')"
  if [ "$n" != "0" ]; then
    run_once || true
  fi
}

list_queue_files | mark_seen
if command -v inotifywait >/dev/null 2>&1; then
  while true; do
    inotifywait -q -e create -e moved_to "$QUEUE_DIR" >/dev/null 2>&1 || true
    list_queue_files | diff_new_files | handle_new_files
    list_queue_files | mark_seen
  done
else
  while true; do
    list_queue_files | diff_new_files | handle_new_files
    list_queue_files | mark_seen
    sleep "$POLL_SEC"
  done
fi
