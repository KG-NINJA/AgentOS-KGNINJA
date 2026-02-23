#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/user/kg-autonomous}"
QUEUE_DIR="${QUEUE_DIR:-$WORKDIR/queue}"
LOGDIR="${LOGDIR:-$WORKDIR/runtime/queue_watch}"
LOCKFILE="${LOCKFILE:-$LOGDIR/lock}"
POLL_SEC="${POLL_SEC:-10}"

mkdir -p "$LOGDIR" "$QUEUE_DIR"
touch "$LOGDIR/seen.txt"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "[$(timestamp)] already running (lock busy). exit."
  exit 0
fi

echo "[$(timestamp)] queue watcher start" | tee -a "$LOGDIR/watcher.log"
echo "[$(timestamp)] WORKDIR=$WORKDIR QUEUE_DIR=$QUEUE_DIR" | tee -a "$LOGDIR/watcher.log"

run_factory_once() {
  local run_id log rc
  run_id="$(date -u +"%Y%m%d_%H%M%S")"
  log="$LOGDIR/run_${run_id}.log"
  echo "[$(timestamp)] trigger run_id=$run_id" | tee -a "$log"

  (
    cd "$WORKDIR"
    bash ./factory.sh run
  ) >>"$log" 2>&1
  rc=$?

  echo "[$(timestamp)] done rc=$rc" | tee -a "$log"
  return "$rc"
}

list_queue_files() { find "$QUEUE_DIR" -maxdepth 1 -type f -name '*.md' 2>/dev/null | sort || true; }
mark_seen() { cat > "$LOGDIR/seen.txt"; }
diff_new_files() {
  local current_file="$LOGDIR/current.txt"
  cat > "$current_file"
  comm -13 "$LOGDIR/seen.txt" "$current_file" || true
}

handle_new_files() {
  local new_count
  new_count="$(wc -l | tr -d ' ')"
  if [ "$new_count" != "0" ]; then
    run_factory_once || true
  fi
}

list_queue_files | mark_seen
if command -v inotifywait >/dev/null 2>&1; then
  echo "[$(timestamp)] mode=inotify" | tee -a "$LOGDIR/watcher.log"
  while true; do
    inotifywait -q -e create -e moved_to "$QUEUE_DIR" >/dev/null 2>&1 || true
    list_queue_files | diff_new_files | tee -a "$LOGDIR/new_files.log" | handle_new_files
    list_queue_files | mark_seen
  done
else
  echo "[$(timestamp)] mode=polling POLL_SEC=$POLL_SEC" | tee -a "$LOGDIR/watcher.log"
  while true; do
    list_queue_files | diff_new_files | tee -a "$LOGDIR/new_files.log" | handle_new_files
    list_queue_files | mark_seen
    sleep "$POLL_SEC"
  done
fi
