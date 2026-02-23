# Failure Injection for Evolution Eval

This mode injects controlled post-generation breakage to test whether kernel hardening reduces failure rates versus baseline.

## Config

`evolution_eval/config.json`:

```json
"failure_injection": {
  "enabled": true,
  "mode": "dependency_error",
  "rate": 0.4,
  "seed": 20260216,
  "baseline_enabled": true,
  "kernel_enabled": false
}
```

- `enabled`: global switch.
- `mode`: current supported value is `dependency_error`.
- `rate`: probability in `[0,1]` applied per successful run.
- `seed`: deterministic seed for reproducible injection choices.
- `baseline_enabled` / `kernel_enabled`: independent toggles.

## Injection behavior

When a run exits `0`, runner may inject failure based on deterministic seeded sampling:

- add invalid dependency `broken-dep: 9999.0.0`, or
- replace `scripts.test` with an invalid command.

Injection metadata is recorded in each run log record:

- `injection_applied: true|false`
- `injection_mode: "dependency_error"|null`

## Analysis outputs

`analyze.py` adds injected-subset metrics in `analysis_extended.json`:

- `baseline_injected.failure_rate`
- `kernel_injected.failure_rate`
- `improvement.injected_absolute_improvement`
- `improvement.injected_relative_improvement`

Interpretation:

- Compare overall failure rates and injected-subset failure rates.
- Kernel hardening is effective when injected-subset failure rate decreases vs baseline.

## Backward compatibility

When `failure_injection.enabled=false`, behavior is unchanged.
