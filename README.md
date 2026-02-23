# Factory OS 1.0 FINAL

Crash-safe autonomous agent operating system.

Factory OS converts text ideas into working software using:

- Persistent queue execution
- Autonomous workers
- Self-healing repair loops
- Codex-based generation
- Session persistence
- Control plane CLI

Fully autonomous.
Restart-safe.
Observable.

Repository:

https://github.com/KG-NINJA/AgentOS-KGNINJA


---------------------------------

## Quick Start

Start Factory OS:

    bash factory.sh start

Submit a job:

    echo "Build a todo app" > queue/incoming/todo.md

Check status:

    bash factory.sh status

Factory OS will automatically:

- lease the job
- generate code
- validate output
- repair failures
- record metrics
- produce artifacts


---------------------------------

## Example Run

Create job:

    echo "Build snake game" > queue/incoming/snake.md

Start OS:

    bash factory.sh start

Observe:

    bash factory.sh status

Result:

    queue/done/snake.md


---------------------------------

## Architecture

Factory OS is a queue-governed autonomous system.

User input:

    queue/incoming/*.md

Execution flow:

    incoming
      → leased
      → build
      → validate
      → repair
      → done | failed

Key components:

Queue Layer

- queue/incoming
- queue/leased
- queue/done
- queue/failed

Execution Layer

- watch_queue.sh
- run_job.sh
- validate.sh

Repair Layer

- codex_fix.sh
- repair daemon
- session manager

Control Plane

- factory.sh CLI

Runtime State

- runtime/task_state.json
- runtime/metrics.jsonl
- runtime/codex_sessions.json


---------------------------------

## Why Factory OS

Typical agents:

User → Prompt → Code

Factory OS:

User → Queue → Autonomous OS → Software


Factory OS provides:

- crash-safe execution
- lease recovery
- persistent sessions
- selftest verification
- repair automation
- runtime metrics


---------------------------------

## Features

### Queue-based execution

Jobs are submitted as markdown files.

Factory OS processes them automatically.


### Lease recovery

Stale jobs are automatically rescued.


### Self repair

Failures trigger automatic repair.


### Persistent sessions

Codex sessions are reused and tracked.


### Control plane

Single command interface:

    factory start
    factory stop
    factory status
    factory repair
    factory selftest


---------------------------------

## Commands

Start OS:

    bash factory.sh start

Stop OS:

    bash factory.sh stop

Check status:

    bash factory.sh status

Repair system:

    bash factory.sh repair

Run selftest:

    bash factory.sh selftest

Version:

    bash factory.sh version


---------------------------------

## Selftest

Factory OS includes a full black-box selftest.

Selftest verifies:

- queue execution
- lease handling
- metrics writing
- repair logic
- session reuse
- API health (if available)

Run:

    bash factory.sh selftest


---------------------------------

## Runtime Files

Factory OS stores runtime state in:

runtime/

Important files:

runtime/task_state.json
runtime/metrics.jsonl
runtime/codex_sessions.json


---------------------------------

## Design Goals

Factory OS was designed to be:

- autonomous
- restart-safe
- observable
- minimal
- script-driven


---------------------------------

## Status

Factory OS 1.0 FINAL

Stable autonomous operation confirmed.


---------------------------------

## License

MIT


---------------------------------
