# evolution_eval

Local Bash + Python experiment pipeline for baseline vs kernel failure-rate comparison.

## Failure Classification

`DECISION_LIMIT` pauses are **excluded** from failures.

Only these are counted as failures:

- `POST_GATE_REJECT:*`
- `HARD_POLICY_BLOCK:*`

## Files

- `config.json`
- `clean_workspace.sh`
- `baseline_runner.sh`
- `kernel_runner.sh`
- `orchestrate.sh`
- `orchestrate_safe.sh`
- `run_logger.py`
- `analyze.py`
- `safety_controller.py`
- `report.py`

## Configuration

Edit `evolution_eval/config.json`:

- `num_runs`: runs per experiment arm
- `workspace_cleanup`: whether to clean state before each runner
- `execution_command`: command to execute each run (default: `./factory.sh run`)
- `failure_definition`: includes DECISION_LIMIT exclusion
- `max_runs`: global hard cap of combined baseline+kernel log entries
- `batch_size`: runs per batch in safe mode
- `max_batches`: maximum batch loop count in safe mode
- `max_duration_seconds`: maximum safe-mode runtime
- `stability_threshold`: stop when one classification dominates at/above this ratio
- `no_improvement_patience`: stop when no improvement persists this many batches

## Step-by-Step Execution

From repository root (`kg-autonomous`):

```bash
# 1) Clean workspace manually
bash evolution_eval/clean_workspace.sh

# 2) Run baseline
bash evolution_eval/baseline_runner.sh

# 3) Run kernel
bash evolution_eval/kernel_runner.sh

# 4) Analyze results
python3 evolution_eval/analyze.py

# 5) Evaluate safety stop state
python3 evolution_eval/safety_controller.py

# 6) View report
python3 evolution_eval/report.py
```

## One-Command Orchestration

```bash
# Legacy single-pass
bash evolution_eval/orchestrate.sh

# Safe batch mode (recommended)
bash evolution_eval/orchestrate_safe.sh
```

## Safe Evolution Mode

Safe mode (`orchestrate_safe.sh`) runs in batches and halts automatically when continuation is unsafe or unproductive.

Stop conditions:

- total runs reaches `max_runs`
- elapsed runtime exceeds `max_duration_seconds`
- most frequent classification ratio exceeds `stability_threshold`
- both baseline and kernel failure rates are 100%
- absolute improvement is <= 0.0 for `no_improvement_patience` consecutive batches

Why this matters:

- Infinite or stagnant experiment loops waste compute and can hide root causes.
- Stable dominant failure signatures indicate the harness is no longer learning.
- Automatic stops protect local environments and make results auditable.

Override limits:

- Edit `evolution_eval/config.json` values directly.
- For temporary run count in a single runner invocation:
  - `RUN_COUNT_OVERRIDE=<N> APPEND_LOGS=1 bash evolution_eval/baseline_runner.sh`
  - `RUN_COUNT_OVERRIDE=<N> APPEND_LOGS=1 bash evolution_eval/kernel_runner.sh`

## Local Command Detection

Runners and orchestrators check whether the configured execution command is locally available.

If unavailable, they print a clear placeholder notice and log placeholder success entries instead of failing.

## Output

Output directory is `output_dir` from config (default: `evolution_eval/output`).

Generated files:

- `baseline_runs.jsonl`
- `kernel_runs.jsonl`
- `analysis_summary.json`
- `analysis_extended.json`
- `safe_batches.log`
- `safety_state.json`
- `safety_last.json`

## Causal Evaluation & Transition Modeling

The evaluation includes a transition-state layer to isolate repair behavior causally.

States:
- `S0`: initial `FAIL`
- `S1`: `repair_attempted == true`
- `S2`: final `SUCCESS`
- `S3`: final `FAIL`

Transition outputs (from JSONL fields only):
- counts and probabilities
- Wilson 95% confidence intervals
- signed Cohen's h (kernel vs baseline) for key transitions (`S1->S2`, `S0->S2`)
- causal repair lift: `P(S2 | repair_attempted) - P(S2 | no_repair)`
- odds ratio with CI for repair vs no repair
- Fisher exact p-value fallback when any contingency cell is small (`<5`)

Causal isolation mode:

```bash
python3 evolution_eval/analyze.py --causal-mode injected_only
```

This analyzes only `injection_applied == true` rows to reduce natural task variance and better isolate repair impact.

Power planning:
- `estimate_required_sample_size()` is included in `analyze.py` and reported in output under `power_estimation`.

Example invocation:

```bash
python3 evolution_eval/analyze.py
python3 evolution_eval/analyze.py --causal-mode injected_only
```
