#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

JOB_ID="factory_selftest_$(date +%s)"
JOB_NAME="${JOB_ID}.md"
INCOMING_PATH="queue/incoming/$JOB_NAME"
LEASED_PATH="queue/leased/$JOB_NAME"
DONE_PATH=""
RESCUED_PREFIX="${JOB_ID}_stuck"
SELFTEST_FAIL=0
FAIL_REASONS=()

fail_step() {
  local reason="$1"
  SELFTEST_FAIL=1
  FAIL_REASONS+=("$reason")
}

cleanup() {
  rm -f queue/incoming/selftest.md "$INCOMING_PATH" "$LEASED_PATH"
  rm -f queue/leased/"${RESCUED_PREFIX}"*.md queue/incoming/"${RESCUED_PREFIX}"*.md
  rm -f queue/done/"${JOB_ID}"*.md queue/failed/"${JOB_ID}"*.md 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p queue/incoming queue/leased queue/done queue/failed
bash factory/os/bootstrap_runtime.sh >/dev/null 2>&1 || true

# Step 1: queue write (queue/incoming/selftest.md)
cat >queue/incoming/selftest.md <<'JOB'
app_id: selftest

Factory selftest request
JOB
if [ ! -f queue/incoming/selftest.md ]; then
  fail_step "queue_write_failed"
fi
if ! mv queue/incoming/selftest.md "$INCOMING_PATH"; then
  fail_step "queue_move_failed"
fi

# Step 2: lease + process job (done or failed)
set +e
LEASE_RESULT="$(bash factory/queue/lease_one.sh 2>/dev/null)"
lease_rc=$?
set -e
if [ "$lease_rc" -ne 0 ] || [ -z "$LEASE_RESULT" ]; then
  fail_step "lease_failed"
else
  LEASED_PATH="$LEASE_RESULT"
  if [ ! -f "$LEASED_PATH" ]; then
    fail_step "leased_file_missing"
  else
    job_base="$(basename "$LEASED_PATH")"
    DONE_PATH="queue/done/$job_base"
    mv "$LEASED_PATH" "$DONE_PATH"
  fi
fi

if [ -n "$DONE_PATH" ] && [ -f "$DONE_PATH" ]; then
  mkdir -p runtime
  printf '{"ts": %s, "job_id": "%s", "app_id": "selftest", "status": "selftest"}\n' "$(date +%s)" "$JOB_ID" >>runtime/metrics.jsonl
else
  fail_step "job_not_processed"
fi

# Step 3: metrics entry detected
if ! grep -q "$JOB_ID" runtime/metrics.jsonl 2>/dev/null; then
  fail_step "metrics_missing"
fi

# Step 4: lease rescue validation
STUCK_PATH="queue/leased/${RESCUED_PREFIX}.md"
if [ -n "$DONE_PATH" ] && [ -f "$DONE_PATH" ]; then
  cp "$DONE_PATH" "$STUCK_PATH"
else
  printf '' >"$STUCK_PATH"
fi
touch -d '2 hours ago' "$STUCK_PATH" 2>/dev/null || touch "$STUCK_PATH"
rescue_output="$(bash factory/queue/rescue_leases.sh 2>&1 || true)"
if compgen -G "queue/leased/${RESCUED_PREFIX}"\*.md >/dev/null 2>&1; then
  fail_step "rescue_not_cleared"
fi
if ! compgen -G "queue/incoming/${RESCUED_PREFIX}"\*.md >/dev/null 2>&1; then
  fail_step "rescue_not_returned"
fi

# Step 5: Codex session reuse verification
session_first="$(SELFTEST_ROOT="$ROOT" python3 - <<'PY' 2>/dev/null || true
import os
import sys
import uuid
from pathlib import Path

root = Path(os.environ["SELFTEST_ROOT"]).resolve()
sys.path.insert(0, str(root / "factory" / "agent"))
from session_manager import SessionManager  # noqa: E402

sm = SessionManager(root)
sid = str(uuid.uuid4())
sm.set_session("selftest", sid)
sm.update_last_used("selftest")
print(sid)
PY
)"
session_first="$(printf '%s' "$session_first" | tr -d '\r' | head -n 1)"
session_cached="$(python3 factory/agent/codex_app_client.py session-id --root "$ROOT" --app-id selftest 2>/dev/null | tr -d '\r' | head -n 1 || true)"
if [ -z "$session_first" ] || [ "$session_first" != "$session_cached" ]; then
  fail_step "codex_session_mismatch"
fi

# Step 6: API health if installed (optional path)
if [ -x factory/api/api_control.sh ]; then
  api_running=0
  if bash factory/api/api_control.sh status >/dev/null 2>&1; then
    api_running=1
  elif bash factory/api/api_control.sh start >/dev/null 2>&1 && bash factory/api/api_control.sh status >/dev/null 2>&1; then
    api_running=1
  fi
  if [ "$api_running" -eq 1 ]; then
    api_ok=0
    for _ in 1 2 3; do
      if python3 - <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception:
    sys.exit(1)
status = str(data.get("status", "")).lower()
sys.exit(0 if status in {"ok", "healthy"} else 1)
PY
      then
        api_ok=1
        break
      fi
      sleep 1
    done
    if [ "$api_ok" -ne 1 ]; then
      fail_step "api_health_unreachable"
    fi
  fi
fi

if [ "$SELFTEST_FAIL" -eq 0 ]; then
  echo "SELFTEST OK"
  exit 0
fi

if [ "${#FAIL_REASONS[@]}" -gt 0 ]; then
  printf 'Selftest detail: %s\n' "${FAIL_REASONS[@]}" >&2
fi
echo "SELFTEST FAIL"
exit 1
