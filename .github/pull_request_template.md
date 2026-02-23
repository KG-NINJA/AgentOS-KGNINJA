## Summary

Describe what changed and why.

## Changes

- 

## Validation

- [ ] `bash -n factory.sh install.sh factory/**/*.sh`
- [ ] `python3 -m py_compile factory/**/*.py diagnostics/*.py evolution_eval/*.py`
- [ ] `bash factory.sh selftest`

## Operational Impact

- [ ] No runtime artifact leakage (`runtime/`, `workspace/`, `.venv/`)
- [ ] Queue behavior unchanged or documented
- [ ] Recovery behavior unchanged or documented
