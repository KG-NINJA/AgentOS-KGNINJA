# Factory OS 1.0 FINAL

Crash-safe autonomous agent operating system.

Factory OS turns text ideas into working software automatically.

Unlike typical coding agents:

User → Prompt → Code

Factory OS:

User → Queue → Autonomous OS → Software

Repository:

https://github.com/KG-NINJA/AgentOS-KGNINJA


---------------------------------

## Why Factory OS Exists

Modern coding agents generate code.

Factory OS runs them as an operating system.

Factory OS provides:

- Persistent execution
- Crash-safe recovery
- Lease-based job control
- Automatic repair loops
- Runtime observability
- Selftest verification

Factory OS keeps running even when jobs fail.


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
- finish execution


---------------------------------

## Example

Create a job:

    echo "Build snake game" > queue/incoming/snake.md

Start OS:

    bash factory.sh start

Check status:

    bash factory.sh status

Result:

    queue/done/snake.md


---------------------------------

## Architecture

Factory OS is a queue-governed autonomous system.

Input:

    queue/incoming/*.md

Execution flow:

    incoming
      → leased
      → build
      → validate
      → repair
      → done | failed

Core components:

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

## Features

### Persistent Queue

Jobs are markdown files.

Factory OS executes them continuously.


### Crash-safe execution

Workers recover automatically after interruption.


### Lease Recovery

Stale jobs are rescued automatically.


### Automatic Repair

Failures trigger repair loops.


### Persistent Sessions

Codex sessions are reused.


### Selftest

Full system verification:

    bash factory.sh selftest


---------------------------------

## Commands

Start:

    bash factory.sh start

Stop:

    bash factory.sh stop

Status:

    bash factory.sh status

Repair:

    bash factory.sh repair

Selftest:

    bash factory.sh selftest

Version:

    bash factory.sh version


---------------------------------

## Runtime

Runtime state:

runtime/

Important files:

runtime/task_state.json

runtime/metrics.jsonl

runtime/codex_sessions.json


---------------------------------

## Design Goals

Factory OS is designed to be:

- Autonomous
- Restart-safe
- Observable
- Script-driven
- Minimal


---------------------------------

## Status

Factory OS 1.0 FINAL

Stable autonomous operation confirmed.


---------------------------------

## License

MIT


---------------------------------
