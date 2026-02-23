# Factory Run Diagnostics

This directory provides local diagnostics tools for repeated `./factory.sh run` failures.

## Files

- `diagnose_factory.sh`: Bash runner that executes repeated runs and captures artifacts.
- `log_diagnoser.py`: Python parser/classifier that builds structured diagnostics output.
- `factory_diagnostics.schema.json`: JSON schema for the diagnostics summary format.

## How To Run

From repository root:

```bash
chmod +x diagnostics/diagnose_factory.sh
python3 --version
bash diagnostics/diagnose_factory.sh 8 80
```

Arguments:

- First arg: number of runs (default `8`)
- Second arg: tail line count from runtime logs (default `80`)

Examples:

```bash
# 5 runs, 120-line tail capture
bash diagnostics/diagnose_factory.sh 5 120

# 10 runs with defaults for tail
bash diagnostics/diagnose_factory.sh 10
```

## What Gets Captured Per Run

Each run writes artifacts under `diagnostics/runs/`:

- `run_XXX.stdout.log`
- `run_XXX.stderr.log`
- `run_XXX.index.tail.log` (tail of `runtime/index.log`)
- `run_XXX.activity.tail.log` (tail of `runtime/activity.log`)

Raw run metadata is appended to:

- `diagnostics/raw_runs.jsonl`

## Classification Rules

`log_diagnoser.py` classifies each run as:

- `REAL_FAILURE`: `POST_GATE status=fail` or explicit `HARD_POLICY_BLOCK` pattern.
- `DECISION_LIMIT`: generation paused due to decision limit.
- `REFLEX/QUALITY_GATE_BLOCK`: reflex emergency block or quality gate fail pattern.
- `UNKNOWN_ERROR`: non-zero exit with no known signature (or unrecognized behavior).

## Output Files

- `diagnostics/factory_diagnostics.json`: machine-readable summary
- `diagnostics/factory_diagnostics.txt`: human-readable report

The JSON summary includes:

- total runs
- classification counts/percentages
- most frequent classification
- reason code distribution
- example stderr snippets per classification
- pattern-based suggested causes and remediation steps
- run-level classification table

## Interpreting Results

1. Check `most_frequent_classification` first.
2. Review `reason_code_distribution` to identify dominant failure signatures.
3. Use `example_stderr_by_classification` to confirm concrete symptom lines.
4. Apply `suggested_remediations` in order, then rerun diagnostics.

## Redirecting and Capturing Outer Logs

You can capture the diagnostics driver output itself:

```bash
bash diagnostics/diagnose_factory.sh 8 80 > diagnostics/driver.stdout.log 2> diagnostics/driver.stderr.log
```

This is useful when debugging wrapper execution around per-run artifacts.

## Notes

- No external network dependencies are required.
- Missing runtime log files are handled gracefully with placeholder text.
