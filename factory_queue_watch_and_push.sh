#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/user/kg-autonomous}"
QUEUE_DIR="${QUEUE_DIR:-$WORKDIR/queue}"
LOGDIR="${LOGDIR:-$WORKDIR/runtime/queue_watch}"
LOCKFILE="${LOCKFILE:-$LOGDIR/lock_push}"
POLL_SEC="${POLL_SEC:-10}"

mkdir -p "$LOGDIR" "$QUEUE_DIR"
touch "$LOGDIR/seen_push.txt"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "[$(timestamp)] already running (lock busy). exit."
  exit 0
fi

run_once() {
  local run_id log rc
  run_id="$(date -u +"%Y%m%d_%H%M%S")"
  log="$LOGDIR/run_push_${run_id}.log"
  echo "[$(timestamp)] trigger run_id=$run_id" | tee -a "$log"

  (
    cd "$WORKDIR"
    bash ./factory.sh run
    bash ./factory_git_push.sh
  ) >>"$log" 2>&1
  rc=$?

  echo "[$(timestamp)] done rc=$rc" | tee -a "$log"
  return "$rc"
}

list_queue_files() { find "$QUEUE_DIR" -maxdepth 1 -type f -name '*.md' 2>/dev/null | sort || true; }
mark_seen() { cat > "$LOGDIR/seen_push.txt"; }
diff_new_files() {
  local current_file="$LOGDIR/current_push.txt"
  cat > "$current_file"
  comm -13 "$LOGDIR/seen_push.txt" "$current_file" || true
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
