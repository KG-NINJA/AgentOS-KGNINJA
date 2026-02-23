#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p runtime runtime/learning

index_backup="runtime/index.log.testbak"
latest_backup="runtime/.last_generated_project.testbak"
all_backup="runtime/learning/all_runs.jsonl.testbak"
success_backup="runtime/learning/success_cases.jsonl.testbak"
fail_backup="runtime/learning/fail_cases.jsonl.testbak"

restore() {
  if [ -f "$index_backup" ]; then mv -f "$index_backup" runtime/index.log; else rm -f runtime/index.log; fi
  if [ -f "$latest_backup" ]; then mv -f "$latest_backup" runtime/.last_generated_project; else rm -f runtime/.last_generated_project; fi
  if [ -f "$all_backup" ]; then mv -f "$all_backup" runtime/learning/all_runs.jsonl; else rm -f runtime/learning/all_runs.jsonl; fi
  if [ -f "$success_backup" ]; then mv -f "$success_backup" runtime/learning/success_cases.jsonl; else rm -f runtime/learning/success_cases.jsonl; fi
  if [ -f "$fail_backup" ]; then mv -f "$fail_backup" runtime/learning/fail_cases.jsonl; else rm -f runtime/learning/fail_cases.jsonl; fi
}
trap restore EXIT

[ ! -f runtime/index.log ] || cp runtime/index.log "$index_backup"
[ ! -f runtime/.last_generated_project ] || cp runtime/.last_generated_project "$latest_backup"
[ ! -f runtime/learning/all_runs.jsonl ] || cp runtime/learning/all_runs.jsonl "$all_backup"
[ ! -f runtime/learning/success_cases.jsonl ] || cp runtime/learning/success_cases.jsonl "$success_backup"
[ ! -f runtime/learning/fail_cases.jsonl ] || cp runtime/learning/fail_cases.jsonl "$fail_backup"

printf '%s\n' 'TOTAL_PROJECTS=1' > runtime/index.log
printf '%s\n' 'workspace/project-001' > runtime/.last_generated_project
printf '%s\n' '2026-02-14T00:00:00+09:00 QUALITY_GATE status=fail score=2 threshold=3 project=workspace/project-001 reason="test failure"' >> runtime/index.log

rm -f runtime/learning/fail_cases.jsonl
./tools/analyze_logs.sh >/dev/null

if [ ! -s runtime/learning/fail_cases.jsonl ]; then
  echo "test_learning_pipeline: fail_cases.jsonl was not written" >&2
  exit 1
fi

if ! tail -n 1 runtime/learning/fail_cases.jsonl | grep -q '"status": "fail"'; then
  echo "test_learning_pipeline: expected fail status record" >&2
  exit 1
fi

echo "test_learning_pipeline: OK"
