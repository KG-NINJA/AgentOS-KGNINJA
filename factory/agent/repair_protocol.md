# Repair Queue Protocol

## Directories

- `runtime/repair_queue/incoming`
- `runtime/repair_queue/leased`
- `runtime/repair_queue/done`
- `runtime/repair_queue/failed`
- `runtime/repair_queue/results`
- `runtime/repair_queue/logs`

## Request JSON schema (minimum)

```json
{
  "job_id": "job_123",
  "app_id": "demo",
  "target_dir": "/home/user/kg-autonomous/workspace/apps/demo",
  "validate_log_path": "/home/user/kg-autonomous/runtime/validate_job_123.log",
  "constraints": {
    "allow_write_root": "/home/user/kg-autonomous/workspace/apps/demo",
    "forbid_paths": [
      "/home/user/kg-autonomous/.git",
      "/home/user/kg-autonomous/.codex",
      "/home/user/kg-autonomous/runtime"
    ]
  }
}
```

`app_id` can be `null`.

## Result JSON schema (minimum)

```json
{
  "job_id": "job_123",
  "request_id": "job_123_1700000000",
  "status": "ok",
  "fail_reason": null,
  "updated_at": "2026-02-22T14:00:00Z"
}
```

On failure:
- `status: failed`
- `fail_reason: timeout | protocol:* | repair-failed | target_outside_allow_root | target_in_forbid_path | daemon:*`

Optional fields:
- `stdout` (tail)
- `stderr` (tail)
- `exit_code`

## Processing model

1. Daemon leases one file (`incoming -> leased`) with `mv` under `flock`.
2. Daemon executes repair through `codex app-server` + `command/exec`.
3. Daemon writes `results/<request_id>.json`.
4. Daemon moves leased file to `done/` or `failed/`.

## Fallback behavior

- If daemon is unavailable, `factory/repair/codex_fix.sh` falls back to direct `codex exec`.
