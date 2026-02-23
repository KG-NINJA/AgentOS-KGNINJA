# Factory OS 1.0 FINAL

Factory OS is a crash-safe autonomous build system that turns text briefs into working applications through a governed queue, deterministic execution, and observable repair loops.

---

## Core Concept

Idea → Queue → Factory → Validate → Repair → Deploy

```
+---------+      +---------+      +-----------+      +-----------+      +---------+      +---------+
|  Idea   | ---> |  Queue  | ---> |  Factory  | ---> | Validate  | ---> | Repair  | ---> | Deploy  |
+---------+      +---------+      +-----------+      +-----------+      +---------+      +---------+
```

Human-authored markdown enters the queue, the factory executes the run pipeline, validators enforce quality, automated repair attempts fixes, and deployments happen only after a verified build.

---

## Design Goals

- **Single-command operation** – Install, start, stop, repair, and test via the `factory.sh` CLI.
- **Crash-safe architecture** – Background workers respawn safely, and `factory start` / `factory repair` are idempotent.
- **Deadlock-safe queue** – Leased jobs are rescued automatically; the queue never stays stuck in a leased state.
- **Self-healing builds** – Validation failures feed into Codex repair loops and session persistence.
- **Observability-first design** – Metrics, task state, and sessions are written to JSONL/JSON for replay and diagnosis.

---

## Architecture

| Layer | Components | Purpose |
|-------|------------|---------|
| **Queue Layer** | `queue/incoming`, `queue/leased`, `queue/done`, `queue/failed` | Persistent mailbox for intents with lease + rescue semantics. |
| **Execution Layer** | `factory/os/watch_queue.sh`, `factory/os/run_job.sh`, `factory/repair/validate.sh` | Dequeues work, runs the gated build pipeline, and records metrics. |
| **Repair Layer** | `factory/repair/codex_fix.sh`, `factory/agent/codex_daemon.py`, `factory/agent/session_manager.py` | Applies Codex-driven fixes, maintains daemon processes, and keeps reusable sessions. |
| **Control Plane** | `factory.sh`, `factory/os/status.sh`, `factory.sh repair`, `factory.sh selftest` | Provides the CLI entry point, health surface, repair automation, and self-test validation. |
| **Runtime** | `runtime/metrics.jsonl`, `runtime/task_state.json`, `runtime/codex_sessions.json` | Central evidence store for job outcomes, orchestration state, and Codex session reuse. |

---

## Command Interface

### Start
```bash
bash factory.sh start
```
Bootstrap runtime directories, rescue stale leases, launch the watch queue, Codex daemon (if installed), optional API server, and validate health.

### Stop
```bash
bash factory.sh stop
```
Stop API, Codex daemon, user systemd services, and watcher processes safely.

### Status
```bash
bash factory.sh status
```
Print queue counts, worker/app/API states, session totals, last metrics entry, and overall health.

### Selftest
```bash
bash factory.sh selftest
```
Run an end-to-end queue enqueue → lease → metrics → session → API smoke test. Outputs `SELFTEST OK` or `SELFTEST FAIL` with detail.

### Repair
```bash
bash factory.sh repair
```
Rescue leases, ensure runtime bootstrapping, restart missing workers/daemons, and report restarted components.

---

## Queue Format

Jobs are markdown files dropped into `queue/incoming`. The header configures routing via simple key-value pairs.

```
app_id: todo_app

Build a minimal todo application.
```

The queue layer handles leasing (`queue/leased`), completion (`queue/done`), and failures (`queue/failed`). `factory/queue/rescue_leases.sh` guarantees recovery of stranded jobs.

---

## Repair System

```
factory run → validate → codex_fix → retry (bounded) → finalize
```

- Validation failures produce logs plus `runtime/metrics.jsonl` entries.
- `codex_fix.sh` consults Codex to apply targeted patches using session context.
- Each repair attempts a bounded number of retries before marking the job failed, keeping artifacts inspectable for manual follow-up.

---

## Codex Integration

- **Persistent sessions** – `runtime/codex_sessions.json` tracks reusable session IDs for each app_id.
- **Repair transport** – The Codex daemon listens through `factory/agent/app_server_control.sh` for stateless commands.
- **Fallback execution** – When Codex is unavailable, CLI commands remain operational; status will show the Codex subsystem as stopped.

---

## Deployment Model

Factory OS focuses on building artifacts deterministically. Deployment is performed downstream through Git push + GitHub Actions (or similar CI) which then publish to the appropriate hosting target. Keeping deploy external maintains separation of concerns and allows any organization-specific pipeline to consume the generated output.

---

## Installation

```bash
bash install.sh
bash factory.sh start
bash factory.sh status
```

The installer prepares queue/runtime directories and enables optional systemd units if present.

---

## Minimal Requirements

- Linux environment (bare metal or server)
- WSL is supported for Windows developers

Codex CLI must be installed for repair functionality, but the rest of the system operates with standard POSIX tooling.

---

## Philosophy

Factory OS is an agent operating system: a persistent idea-to-software environment with explicit control planes, safety gates, and repair loops. It is **not** a CI pipeline or a loose script collection; every workflow is routed through the queue and CLI.

---

## Reliability Model

**Guaranteed:**
- Restart-safe recovery (`factory start` and `factory repair` are idempotent)
- Lease recovery via `rescue_leases.sh`
- Repairable builds with bounded retries

**Not guaranteed:**
- Perfect builds. Human review and downstream CI should still evaluate artifacts.

---

## Roadmap

- Distributed workers for horizontal scaling
- Remote agents coordinating specialized skills
- Metrics anomaly detection on `runtime/metrics.jsonl`

---

## Version

Factory OS 1.0 FINAL
