# Factory OS 1.0 FINAL

Factory OS is a persistent autonomous software runtime.

It continuously consumes tasks from a queue
and produces software artifacts.

Factory OS is not a chatbot.
It is not a prompt tool.

It is an always-running autonomous execution system.



------------------------------------------------------------

## Overview

Factory OS is an experiment in building a persistent
autonomous agent runtime.

The system is designed to:

- run continuously
- execute queued tasks
- survive crashes
- recover incomplete jobs
- repair failed outputs
- maintain runtime state

The primary goal is reliability before intelligence.



------------------------------------------------------------

## Execution Model

Factory OS operates as a queue-driven runtime.

Jobs are text files placed in:


queue/incoming/


The runtime moves jobs through:


incoming → leased → done | failed


A worker daemon continuously executes jobs.

Each job runs through:


generate → validate → repair → validate → finalize


No interactive prompting is required.



------------------------------------------------------------

## System Components

Factory OS consists of several persistent subsystems.


### Queue Layer

Responsible for job scheduling.

Directories:


queue/incoming
queue/leased
queue/done
queue/failed


Leasing prevents duplicate execution.

Lease recovery restores abandoned jobs.



### Worker Runtime

watch_queue.sh runs continuously.

Responsibilities:

- lease jobs
- execute builds
- record metrics
- handle failures



### Repair Runtime

Failures trigger repair attempts.

Repair is performed by Codex.

Repair loop:


validate → repair → validate




### Control Plane

factory.sh provides system control.

Commands:


factory start
factory stop
factory restart
factory status
factory repair
factory selftest




### Runtime State

Runtime state is persisted in:


runtime/


Important files:


runtime/task_state.json
runtime/metrics.jsonl
runtime/codex_sessions.json


The system is restart-safe.



------------------------------------------------------------

## Reliability Mechanisms


### Crash Safety

Worker restarts do not lose jobs.

Incomplete jobs remain leased
until recovered.



### Lease Rescue

Stale leases are detected automatically.

Abandoned jobs return to incoming.



### Repair Loop

Failed outputs trigger automatic repair attempts.



### Persistent Sessions

Codex sessions persist across jobs.



------------------------------------------------------------

## Selftest

Factory OS includes a black-box selftest.

Selftest verifies:

- queue execution
- lease handling
- metrics recording
- repair execution
- session reuse

Run:


bash factory.sh selftest




------------------------------------------------------------

## Philosophy

Most coding agents operate interactively:

User → Prompt → Output


Factory OS operates continuously:

Queue → Runtime → Artifacts


Factory OS treats software generation as
a long-running process rather than
a sequence of prompts.



------------------------------------------------------------

## Status

Factory OS 1.0 FINAL

Stable autonomous runtime confirmed.



------------------------------------------------------------

## License

MIT


------------------------------------------------------------
