#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p runtime/learning

if [ -x ./tools/analyze_logs.sh ]; then
  ./tools/analyze_logs.sh >/dev/null
fi

python3 - <<'PY'
import json
import os
from collections import Counter

learning_dir = os.path.join(os.getcwd(), "runtime", "learning")
success_path = os.path.join(learning_dir, "success_cases.jsonl")
fail_path = os.path.join(learning_dir, "fail_cases.jsonl")
out_path = os.path.join(learning_dir, "feedback_prompt.txt")
summary_path = os.path.join(learning_dir, "feedback_summary.json")


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows

success = read_jsonl(success_path)
fails = read_jsonl(fail_path)

recent_success = success[-20:]
recent_fail = fails[-20:]

s_task = Counter(r.get("ai_task", "unknown") for r in recent_success)
f_task = Counter(r.get("ai_task", "unknown") for r in recent_fail)
s_ptype = Counter(r.get("project_type", "unknown") for r in recent_success)
f_ptype = Counter(r.get("project_type", "unknown") for r in recent_fail)
f_reason = Counter(
    (r.get("failure_summary", {}) or {}).get("reason_code")
    or (r.get("post_gate", {}) or {}).get("reason_code")
    or "unknown"
    for r in recent_fail
)

pass_scores = [r.get("quality_gate", {}).get("score") for r in recent_success if isinstance(r.get("quality_gate", {}).get("score"), int)]
fail_scores = [r.get("quality_gate", {}).get("score") for r in recent_fail if isinstance(r.get("quality_gate", {}).get("score"), int)]

avg_pass = round(sum(pass_scores) / len(pass_scores), 2) if pass_scores else None
avg_fail = round(sum(fail_scores) / len(fail_scores), 2) if fail_scores else None

lines = []
lines.append("Use this feedback strictly. Reduce ambiguity and maximize pass probability.")
lines.append("")
lines.append(f"Recent success cases: {len(recent_success)}")
lines.append(f"Recent fail cases: {len(recent_fail)}")
if avg_pass is not None:
    lines.append(f"Avg pass quality score: {avg_pass}")
if avg_fail is not None:
    lines.append(f"Avg fail quality score: {avg_fail}")
lines.append("")

if s_task:
    top = ", ".join(f"{k}:{v}" for k, v in s_task.most_common(3))
    lines.append(f"Success ai_task patterns: {top}")
if s_ptype:
    top = ", ".join(f"{k}:{v}" for k, v in s_ptype.most_common(3))
    lines.append(f"Success project_type patterns: {top}")
if f_task:
    top = ", ".join(f"{k}:{v}" for k, v in f_task.most_common(3))
    lines.append(f"Fail ai_task patterns: {top}")
if f_ptype:
    top = ", ".join(f"{k}:{v}" for k, v in f_ptype.most_common(3))
    lines.append(f"Fail project_type patterns: {top}")

lines.append("")
lines.append("Generation rules:")
lines.append("- Always satisfy required files and runnable setup first.")
lines.append("- Keep UI state transitions explicit and deterministic.")
lines.append("- Include simulation update loop and balancing constants when simulation_level is medium/high.")
lines.append("- Prefer concrete API contracts and strict JSON response shape.")
lines.append("- Add tests that cover visible UI behavior and API schema shape.")
lines.append("- For desktop_app, emit PySide6 + mss + OpenCV/SSIM modules and markdown logger paths from spec.requirements.")

top_reasons = [k for k, _ in f_reason.most_common(3) if k and k != "unknown"]
if top_reasons:
    lines.append("")
    lines.append("Failure-driven hardening rules:")
    if "json_syntax_failed" in top_reasons:
        lines.append("- Validate package.json/core/package.json as strict JSON before finishing output.")
    if "js_syntax_failed" in top_reasons:
        lines.append("- Run self-check for JS syntax mentally (template literals, quotes, braces) before final output.")
    if "install_failed" in top_reasons:
        lines.append("- Keep dependency declarations minimal and correct; avoid invalid version strings or malformed quotes.")
    if "require_check_failed" in top_reasons:
        lines.append("- Ensure every runtime import has matching dependency entry in package.json at actual runtime root.")
    if "tests_failed" in top_reasons:
        lines.append("- Make test commands executable with declared devDependencies and align API/UI contracts to tests.")
        lines.append("- If scripts.test uses jest, generated tests must use Jest APIs (describe/it/expect), not node:test APIs.")
    if "malformed_quote_artifact" in top_reasons:
        lines.append("- Never output broken quote artifacts (e.g., \"''^, \"'!, \"'`); regenerate lines cleanly if uncertain.")
    if "missing_required_file" in top_reasons:
        lines.append("- Always emit required baseline files (core/server.js, core/public/app.js) for web_app.")
    if "missing_python_file" in top_reasons:
        lines.append("- Always emit desktop baseline files (core/app.py, core/ui/main_window.py, core/services/*, tests/test_diff_detector.py).")
    if "missing_dependency_decl" in top_reasons:
        lines.append("- Keep requirements.txt aligned with imported desktop dependencies (PySide6, mss, opencv-python, scikit-image).")
    if "py_compile_failed" in top_reasons:
        lines.append("- Ensure Python modules compile cleanly before finishing output (imports + syntax).")
    if "pytest_failed" in top_reasons:
        lines.append("- Ensure pytest diff/logging tests pass with deterministic fixtures and no GUI-only side effects.")

with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines).strip() + "\n")

summary = {
    "recent_success": len(recent_success),
    "recent_fail": len(recent_fail),
    "avg_pass_score": avg_pass,
    "avg_fail_score": avg_fail,
    "top_success_ai_task": s_task.most_common(5),
    "top_fail_ai_task": f_task.most_common(5),
    "top_success_project_type": s_ptype.most_common(5),
    "top_fail_project_type": f_ptype.most_common(5),
    "top_fail_reason_code": f_reason.most_common(5),
}

with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
PY

echo "FEEDBACK_PACK: updated runtime/learning/feedback_prompt.txt"
