#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v codex >/dev/null 2>&1; then
  echo "WARNING: codex CLI not found. Install Codex CLI before running repair flows."
fi

mkdir -p \
  queue \
  queue/incoming \
  queue/leased \
  queue/processing \
  queue/done \
  queue/failed \
  repair_queue \
  runtime \
  runtime/logs \
  runtime/pid \
  runtime/tmp \
  workspace

bash factory/os/bootstrap_runtime.sh

USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
FACTORY_OS_UNIT="$USER_SYSTEMD_DIR/factory-os.service"
CODEX_DAEMON_UNIT="$USER_SYSTEMD_DIR/codex-daemon.service"

if [ -f "$FACTORY_OS_UNIT" ] || [ -f "$CODEX_DAEMON_UNIT" ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1; then
    if [ -f "$FACTORY_OS_UNIT" ]; then
      systemctl --user enable factory-os.service
    fi

    if [ -f "$CODEX_DAEMON_UNIT" ]; then
      systemctl --user enable codex-daemon.service
    fi
  else
    echo "systemd user session unavailable; service enable skipped."
  fi
fi

echo "Factory OS installed."
echo "Next steps:"
echo "factory start"
echo "factory status"
echo "factory selftest"
