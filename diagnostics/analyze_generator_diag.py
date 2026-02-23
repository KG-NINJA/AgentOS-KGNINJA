#!/usr/bin/env python3
"""Aggregate per-run generator diagnostics into one summary JSON."""

import argparse
import json
import os
from collections import Counter


def load_json(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', required=True)
    parser.add_argument('--output-file', required=True)
    args = parser.parse_args()

    run_files = sorted(
        p for p in os.listdir(args.input_dir)
        if p.startswith('run_') and p.endswith('.json')
    )

    runs = []
    primary_rc = Counter()
    fallback_attempted = Counter()
    fallback_invocation = Counter()
    fallback_model = Counter()
    fallback_rc = Counter()
    final_exit_source = Counter()
    exit_code = Counter()

    for name in run_files:
        row = load_json(os.path.join(args.input_dir, name))
        runs.append(row)
        primary_rc[str(row.get('generator_primary_rc'))] += 1
        fallback_attempted['true' if row.get('fallback_attempted') else 'false'] += 1
        fallback_invocation[str(row.get('generator_fallback_invocation'))] += 1
        fallback_model[str(row.get('generator_fallback_model'))] += 1
        fallback_rc[str(row.get('generator_fallback_rc'))] += 1
        final_exit_source[str(row.get('generator_final_exit_source'))] += 1
        exit_code[str(row.get('run_exit_code'))] += 1

    payload = {
        'total_runs': len(runs),
        'summary': {
            'primary_rc_distribution': dict(primary_rc),
            'fallback_attempted_distribution': dict(fallback_attempted),
            'fallback_invocation_distribution': dict(fallback_invocation),
            'fallback_model_distribution': dict(fallback_model),
            'fallback_rc_distribution': dict(fallback_rc),
            'final_exit_source_distribution': dict(final_exit_source),
            'run_exit_code_distribution': dict(exit_code),
        },
        'runs': runs,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)
    with open(args.output_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
