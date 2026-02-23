#!/usr/bin/env python3
"""Preflight gate: run a few baseline checks and stop if dependency errors dominate."""

import json
import os
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')


def read_json(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def load_jsonl(path: str) -> list[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def main() -> int:
    cfg = read_json(CONFIG_PATH)
    out_dir = resolve_path(cfg['output_dir'])
    baseline_path = os.path.join(out_dir, cfg['logs']['baseline'])

    rows = load_jsonl(baseline_path)
    reason_counts = Counter(str(r.get('reason_code', 'UNKNOWN')) for r in rows)

    dep_errors = reason_counts.get('DEPENDENCY_ERROR', 0)
    threshold = int(cfg.get('preflight', {}).get('dependency_error_stop_threshold', 2))

    should_stop = dep_errors >= threshold
    result = {
        'should_stop': should_stop,
        'reason': 'preflight_dependency_error_threshold_exceeded' if should_stop else 'continue',
        'diagnostics': {
            'runs': len(rows),
            'dependency_error_count': dep_errors,
            'threshold': threshold,
            'reason_counts': dict(reason_counts),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
