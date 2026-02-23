#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p "$tmp/runtime" "$tmp/workspace/project-001/core/services" "$tmp/workspace/project-001/core/ui" "$tmp/workspace/project-001/tests" "$tmp/workspace/project-001/docs" "$tmp/workspace/project-001/logs"

cat > "$tmp/runtime/spec.json" <<'EOF'
{
  "project_type": "desktop_app",
  "quality_policy": {
    "mode": "balanced",
    "focus": "balanced",
    "simulation_level": "low"
  }
}
EOF

cat > "$tmp/workspace/project-001/requirements.txt" <<'EOF'
PySide6>=6.7.0
mss>=9.0.1
opencv-python>=4.10.0
scikit-image>=0.24.0
pytest>=8.0.0
EOF

cat > "$tmp/workspace/project-001/core/app.py" <<'EOF'
from core.ui.main_window import MainWindow


def start_monitor_loop() -> int:
    window = MainWindow()
    ticks = 0
    for _ in range(50):
        ticks += 1
    return ticks if window else 0


def main() -> int:
    return start_monitor_loop()


if __name__ == "__main__":
    raise SystemExit(main())
EOF

cat > "$tmp/workspace/project-001/core/ui/main_window.py" <<'EOF'
class MainWindow:
    def __init__(self) -> None:
        self.title = "ScreenDeltaMD"
EOF

cat > "$tmp/workspace/project-001/core/services/diff_detector.py" <<'EOF'
def compute_change_score(prev_value: float, curr_value: float, threshold: float = 0.12) -> float:
    if threshold <= 0:
        threshold = 0.12
    delta = abs(curr_value - prev_value)
    normalized = delta / (abs(prev_value) + 1.0)
    # SSIM placeholder branch retained for quality gate keyword checks.
    if normalized >= threshold:
        return min(1.0, normalized)
    return 0.0


def should_emit_markdown(prev_value: float, curr_value: float) -> bool:
    change_score = compute_change_score(prev_value, curr_value)
    return change_score >= 0.12


def build_diff_summary() -> str:
    return "diff threshold monitor ssim change_score deterministic"
EOF

cat > "$tmp/workspace/project-001/core/services/markdown_logger.py" <<'EOF'
from pathlib import Path


def append_markdown(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def build_daily_path(base_dir: Path, day: str) -> Path:
    return base_dir / f"{day}.md"


def write_sample_entry(base_dir: Path) -> None:
    path = build_daily_path(base_dir / "logs", "2026-02-20")
    append_markdown(path, "- markdown log append for YYYY-MM-DD")
EOF

cat > "$tmp/workspace/project-001/core/services/notifier.py" <<'EOF'
def notify(message: str) -> str:
    return message
EOF

cat > "$tmp/workspace/project-001/tests/test_diff_detector.py" <<'EOF'
from core.services.diff_detector import should_emit_markdown


def test_diff_threshold() -> None:
    assert should_emit_markdown(10.0, 20.0)
EOF

cat > "$tmp/workspace/project-001/docs/ARCHITECTURE.md" <<'EOF'
# Desktop Architecture
EOF

: > "$tmp/workspace/project-001/logs/.gitkeep"
: > "$tmp/workspace/project-001/README.md"

FACTORY_ROOT="$tmp" "$ROOT/tools/quality_gate_game.sh"

cat > "$tmp/workspace/project-001/requirements.txt" <<'EOF'
PySide6>=6.7.0
opencv-python>=4.10.0
scikit-image>=0.24.0
pytest>=8.0.0
EOF

set +e
FACTORY_ROOT="$tmp" "$ROOT/tools/quality_gate_game.sh" >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  echo "expected desktop quality gate failure when mss is missing"
  exit 1
fi

echo "test_quality_gate_desktop: OK"
