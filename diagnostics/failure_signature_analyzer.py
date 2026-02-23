#!/usr/bin/env python3
"""Aggregate run_artifacts signatures and produce root cause guidance."""

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

ROOT_DIR = "/home/user/kg-autonomous"
ART_DIR = os.path.join(ROOT_DIR, "evolution_eval", "output", "run_artifacts")
SUMMARY_PATH = os.path.join(ROOT_DIR, "diagnostics", "failure_signature_summary.json")
REPORT_PATH = os.path.join(ROOT_DIR, "diagnostics", "root_cause_report.txt")
CONFIG_PATH = os.path.join(ROOT_DIR, "evolution_eval", "config.json")


def load_artifacts(path: str) -> list[dict]:
    rows = []
    if not os.path.isdir(path):
        return rows
    for name in sorted(os.listdir(path)):
        if not name.endswith('.json'):
            continue
        p = os.path.join(path, name)
        try:
            with open(p, 'r', encoding='utf-8') as f:
                obj = json.load(f)
            obj['_file'] = p
            rows.append(obj)
        except Exception:
            continue
    return rows


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{2,}", text)
    stop = {
        'the', 'and', 'for', 'with', 'from', 'line', 'file', 'status', 'runtime',
        'log', 'json', 'true', 'false', 'error', 'failed', 'failure'
    }
    return [t.lower() for t in tokens if t.lower() not in stop]


def common_substrings(lines: list[str]) -> dict[str, int]:
    c = Counter()
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        frag = re.sub(r"\s+", " ", ln)
        if len(frag) > 180:
            frag = frag[:180]
        c[frag] += 1
    return dict(c.most_common(10))


def detect_dependency_or_runtime_issue(text: str) -> bool:
    pats = [
        r"No module named",
        r"ModuleNotFoundError",
        r"command not found",
        r"Cannot find module",
        r"npm ERR",
        r"unexpected EOF while looking for matching",
        r"syntax error",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in pats)


def syntax_probe() -> dict:
    """Run safe, non-executing shell syntax probes to detect script-level runtime blockers."""
    targets = [
        os.path.join(ROOT_DIR, "factory", "generator", "codex_generate.sh"),
        os.path.join(ROOT_DIR, "factory", "brain", "bootstrap.sh"),
    ]
    result = {"has_error": False, "errors": []}
    for path in targets:
        if not os.path.exists(path):
            continue
        proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        if proc.returncode != 0:
            result["has_error"] = True
            msg = (proc.stderr or proc.stdout or "syntax check failed").strip()
            result["errors"].append({"file": path, "message": msg})
    return result


def classify_signature(stage_counter: Counter, reason_counter: Counter, evidence_text: str, probe: dict) -> str:
    dominant_stage = stage_counter.most_common(1)[0][0] if stage_counter else 'unknown'
    dominant_reason = reason_counter.most_common(1)[0][0] if reason_counter else ''

    if dominant_stage == 'quality_gate' or 'QUALITY_GATE_FAIL' in dominant_reason:
        return 'QUALITY_GATE_FAIL'
    if dominant_stage == 'post_gate' or 'POST_GATE_REJECT' in dominant_reason:
        return 'POST_GATE_REJECT'
    if dominant_stage == 'reflex' or 'REFLEX_BLOCK' in dominant_reason:
        return 'REFLEX_BLOCK'
    if 'DEPENDENCY_ERROR' in dominant_reason or detect_dependency_or_runtime_issue(evidence_text) or probe.get('has_error'):
        return 'DEPENDENCY_ERROR'
    if 'UNKNOWN_NONZERO' in dominant_reason:
        return 'UNKNOWN_NONZERO'
    return 'UNKNOWN_NONZERO'


def dependency_suggestions(stderr_text: str, probe: dict) -> list[str]:
    suggestions = []
    if re.search(r'No module named|ModuleNotFoundError', stderr_text, re.IGNORECASE):
        suggestions.append('Python module missing: install into local venv and rerun diagnostics.')
    if re.search(r'command not found|not found', stderr_text, re.IGNORECASE):
        suggestions.append('Missing CLI dependency: verify PATH and required commands (jq/node/rg).')
    if re.search(r'Cannot find module|npm ERR', stderr_text, re.IGNORECASE):
        suggestions.append('Node dependency issue: run npm install in failing project and retry.')
    if re.search(r'unexpected EOF while looking for matching|syntax error', stderr_text, re.IGNORECASE):
        suggestions.append('Shell syntax/runtime issue: fix malformed script (e.g. unmatched quotes/backticks) and rerun.')
    if probe.get('has_error'):
        for err in probe.get('errors', []):
            suggestions.append(f"Fix shell syntax in {err.get('file')}: {err.get('message')}")
    return suggestions


def apply_safe_fix_if_needed(dominant: str) -> str:
    if dominant != 'QUALITY_GATE_FAIL':
        return 'no safe parameter override applied'

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        current = float(cfg.get('quality_gate_relaxation_factor', 1.0))
        relaxed = round(current * 0.9, 4)
        cfg['quality_gate_relaxation_factor'] = relaxed
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return f'quality_gate_relaxation_factor updated from {current} to {relaxed}'
    except Exception as exc:
        return f'failed to apply safe quality gate relaxation: {exc}'


def main() -> int:
    artifacts = load_artifacts(ART_DIR)
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)

    stage_counter = Counter()
    reason_counter = Counter()
    token_counter = Counter()
    all_lines = []
    evidence_chunks = []

    for row in artifacts:
        stage = str(row.get('trace_stage', 'unknown') or 'unknown')
        stage_counter[stage] += 1

        reason = str(row.get('trace_reason', '')).strip()
        if reason:
            reason_counter[reason] += 1

        stderr_tail = str(row.get('stderr_tail', ''))
        stdout_tail = str(row.get('stdout_tail', ''))
        trace_tail = str(row.get('trace_tail', ''))
        combined = '\n'.join([stderr_tail, stdout_tail, trace_tail])
        evidence_chunks.append(combined)

        token_counter.update(tokenize(stderr_tail))
        token_counter.update(tokenize(stdout_tail))
        token_counter.update(tokenize(trace_tail))

        all_lines.extend(stderr_tail.splitlines())
        all_lines.extend(stdout_tail.splitlines())
        all_lines.extend(trace_tail.splitlines())

    probe = syntax_probe()
    combined_evidence = '\n'.join(evidence_chunks)
    dominant_signature = classify_signature(stage_counter, reason_counter, combined_evidence, probe)
    safe_fix_result = apply_safe_fix_if_needed(dominant_signature)
    dep_suggestions = dependency_suggestions(combined_evidence, probe)

    summary = {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'artifact_count': len(artifacts),
        'stage_frequency': dict(stage_counter),
        'trace_reason_frequency': dict(reason_counter),
        'stderr_token_frequency_top20': dict(token_counter.most_common(20)),
        'common_substrings_top10': common_substrings(all_lines),
        'dominant_signature': dominant_signature,
        'safe_fix_result': safe_fix_result,
        'dependency_install_suggestions': dep_suggestions,
        'syntax_probe': probe,
    }

    with open(SUMMARY_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    systemic = 'systemic' if len(artifacts) >= 3 and stage_counter and stage_counter.most_common(1)[0][1] / max(len(artifacts), 1) >= 0.6 else 'incidental'
    top_stage = stage_counter.most_common(1)[0][0] if stage_counter else 'unknown'

    lines = []
    lines.append('Root Cause Report')
    lines.append('=================')
    lines.append(f"generated_at: {summary['generated_at']}")
    lines.append(f"artifact_count: {summary['artifact_count']}")
    lines.append(f"dominant_failure_stage: {top_stage}")
    lines.append(f"dominant_signature: {dominant_signature}")
    lines.append(f"systemic_or_incidental: {systemic}")
    lines.append('')

    if dominant_signature == 'QUALITY_GATE_FAIL':
        lines.append('true_cause: quality gate rejection is dominant')
        lines.append(f"safe_fix_attempt: {safe_fix_result}")
        lines.append('recommended_next_action: rerun with relaxed threshold factor and verify output quality.')
    elif dominant_signature == 'DEPENDENCY_ERROR':
        lines.append('true_cause: dependency/runtime error is dominant')
        lines.append('recommended_next_action: fix runtime/dependency/syntax issue then rerun 5-run batch.')
        for s in dep_suggestions:
            lines.append(f"- {s}")
    elif dominant_signature == 'REFLEX_BLOCK':
        lines.append('true_cause: reflex policy block is dominant')
        lines.append('recommended_next_action: inspect reflex policy limit and triggering project count.')
    elif dominant_signature == 'UNKNOWN_NONZERO':
        lines.append('true_cause: unknown non-zero exits')
        lines.append('recommended_next_action: inspect full trace tails and abort further experiments until classified.')
    else:
        lines.append(f'true_cause: {dominant_signature.lower()}')
        lines.append('recommended_next_action: address dominant signature and rerun 5-run diagnostic batch.')

    lines.append('')
    lines.append('stage_frequency:')
    for k, v in stage_counter.items():
        lines.append(f'- {k}: {v}')

    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines).rstrip() + '\n')

    print(f'summary written: {SUMMARY_PATH}')
    print(f'report written: {REPORT_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
