#!/usr/bin/env python3
"""Experiment validity gate: ensure comparison is meaningful."""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')


def read_json(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def ratio_for_reason(summary: dict, reason: str) -> float:
    total = int(summary.get('total_runs', 0))
    if total == 0:
        return 0.0
    cnt = int(summary.get('reason_code_distribution', {}).get(reason, 0))
    return cnt / total


def main() -> int:
    cfg = read_json(CONFIG_PATH)
    out_dir = resolve_path(cfg['output_dir'])
    analysis_path = os.path.join(out_dir, cfg['logs']['analysis_extended'])

    if not os.path.exists(analysis_path):
        print(json.dumps({
            'is_valid': False,
            'reason': 'analysis_missing',
            'diagnostics': {}
        }, ensure_ascii=False, indent=2))
        return 0

    analysis = read_json(analysis_path)
    baseline = analysis.get('baseline', {})
    kernel = analysis.get('kernel', {})

    b_rate = float(baseline.get('failure_rate', 0.0))
    k_rate = float(kernel.get('failure_rate', 0.0))

    dep_ratio_max = float(cfg.get('validity', {}).get('dependency_error_ratio_max', 0.2))
    b_dep_ratio = ratio_for_reason(baseline, 'DEPENDENCY_ERROR')
    k_dep_ratio = ratio_for_reason(kernel, 'DEPENDENCY_ERROR')

    require_mixed = bool(cfg.get('validity', {}).get('require_mixed_rates', True))
    mixed_ok = (0.0 < b_rate < 1.0) and (0.0 < k_rate < 1.0)

    dep_ok = (b_dep_ratio < dep_ratio_max) and (k_dep_ratio < dep_ratio_max)

    is_valid = dep_ok and (mixed_ok if require_mixed else True)
    reason = 'valid' if is_valid else 'invalid_experiment_conditions'

    print(json.dumps({
        'is_valid': is_valid,
        'reason': reason,
        'diagnostics': {
            'baseline_failure_rate': b_rate,
            'kernel_failure_rate': k_rate,
            'baseline_dependency_error_ratio': b_dep_ratio,
            'kernel_dependency_error_ratio': k_dep_ratio,
            'dependency_error_ratio_max': dep_ratio_max,
            'mixed_rate_required': require_mixed,
            'mixed_rate_ok': mixed_ok,
            'dependency_ratio_ok': dep_ok,
        }
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
