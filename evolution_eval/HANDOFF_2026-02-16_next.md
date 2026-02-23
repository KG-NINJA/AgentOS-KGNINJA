# Handoff (2026-02-16)

## Current Status
- Refactor completed for publication-grade modularity and determinism.
- Test suite passes: `python3 -m unittest discover` -> **36 tests OK**.

## Major Changes Completed

### 1) Kernel orchestration Python migration
- Added: `evolution_eval/kernel_orchestrator.py`
  - Strict status normalization (`SUCCESS` / `FAIL` only; invalid -> `ValueError`)
  - Strict repair trigger: only when `status == "FAIL"` and `reason_code == "DEPENDENCY_ERROR"`
  - Deterministic artifact writing and testable dependency-injection design
- Updated: `evolution_eval/kernel_runner.sh`
  - Thin wrapper only:
    - `python3 -m evolution_eval.kernel_orchestrator "$@"`

### 2) Analysis system structural hardening
- Refactored `evolution_eval/analyze.py` to CLI + orchestration role.
- Added new pure modules:
  - `evolution_eval/stats_core.py`
    - Wilson CI, z-test, Cohen's h, interpretation, required sample size, theoretical power
  - `evolution_eval/null_simulation.py`
    - Null simulation runner (seed-deterministic)
  - `evolution_eval/sensitivity.py`
    - Sensitivity sweep
    - empirical + theoretical power
    - power gap metric
    - deterministic CSV export
  - `evolution_eval/reproducibility.py`
    - SHA256 hashes (file/config/code/input/sweep)
    - reproducibility report payload

### 3) New CLI capabilities
- `--repro-report <path>`
- `--sweep-output <path>`
- Existing options retained (`--null-sim`, `--sensitivity-sweep`, etc.)

### 4) Determinism/reproducibility constraints implemented
- JSON outputs serialized with `sort_keys=True`
- Sweep rows sorted deterministically
- CSV floats fixed to 6 decimals
- Seed-driven simulation paths

## Tests Added / Updated
- `test_kernel_orchestrator.py`
- `test_sensitivity_sweep.py`
- `test_modular_integrity.py`
- `test_empirical_vs_theoretical_power.py`
- `test_reproducibility_artifact.py`
- `test_csv_determinism.py`
- Existing tests remain and pass.

## Quick Run Commands (next session)

### Run full tests
```bash
cd /home/user/kg-autonomous
python3 -m unittest discover
```

### Run analysis + sweep + repro artifact
```bash
cd /home/user/kg-autonomous
python3 evolution_eval/analyze.py \
  --sensitivity-sweep \
  --sweep-sample-sizes 20,50,100,200 \
  --sweep-baseline-rates 0.4,0.5 \
  --sweep-kernel-rates 0.5,0.6 \
  --sweep-injection-rates 0.3,0.5 \
  --sweep-sim 500 \
  --sweep-seed 42 \
  --sweep-output evolution_eval/output/sweep.csv \
  --repro-report evolution_eval/output/repro.json
```

## Known Notes
- `run_logger.py` still has legacy lowercase return paths (`"failure"`) in some branches. Kernel orchestrator currently canonicalizes to `FAIL` to preserve behavior.
- Backward compatibility retained for analysis output keys.

## Files most recently touched
- `evolution_eval/analyze.py`
- `evolution_eval/stats_core.py`
- `evolution_eval/null_simulation.py`
- `evolution_eval/sensitivity.py`
- `evolution_eval/reproducibility.py`
- `evolution_eval/kernel_orchestrator.py`
- `evolution_eval/kernel_runner.sh`
- `test_kernel_orchestrator.py`
- `test_sensitivity_sweep.py`
- `test_modular_integrity.py`
- `test_empirical_vs_theoretical_power.py`
- `test_reproducibility_artifact.py`
- `test_csv_determinism.py`

