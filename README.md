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

# For AI Agents

## OpenClaw Instructions
Specific instructions for the OpenClaw agent to use this service can be found in [OPENCLAW_INSTRUCTIONS.md](OPENCLAW_INSTRUCTIONS.md).

## Service Purpose
AgentOS-KGNINJA / Factory OS provides a structured AI-agent diagnosis API and a self-healing runtime foundation. It is designed to be a queue-oriented repair gateway that AI agents can use to diagnose infrastructure and runtime errors.

## Capability Discovery
Agents can discover the system's capabilities and public API endpoints at:
`GET /.well-known/ai-capabilities.json`

## Diagnosis API
The diagnosis API provides deterministic, rule-based repair guidance for common infrastructure errors.

### Usage Example
```bash
curl -X POST https://example.com/api/diagnose \
  -H "Content-Type: application/json" \
  -d '{
    "error_log": "Timed out after 60s waiting for gateway port 18789",
    "environment": "ubuntu",
    "service": "openclaw-gateway"
  }'
```

### Input Schema
```json
{
  "type": "object",
  "properties": {
    "error_log": { "type": "string" },
    "environment": { "type": "string" },
    "service": { "type": "string" }
  },
  "required": ["error_log"]
}
```

### Output Schema
```json
{
  "type": "object",
  "properties": {
    "summary": { "type": "string" },
    "probable_causes": { "type": "array", "items": { "type": "string" } },
    "safe_first_steps": { "type": "array", "items": { "type": "string" } },
    "risk_level": { "enum": ["low", "medium", "high"] },
    "requires_human_confirmation": { "type": "boolean" }
  }
}
```

## Agentic Commerce Integration

This repository includes a **paid error‑fixing service** for AI agents. Agents can programmatically purchase a diagnostic report and fix suggestions for a failed command or API call. The service leverages Stripe’s **Machine Payments Protocol (MPP)** and **Link agent wallet** running on the **Tempo** blockchain. These technologies enable micro‑payments in stablecoins or card tokens, while granting agents restricted spending authority with human approval flows.

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agentic-commerce.json` | GET | Returns the product metadata, including pricing (500 JPY), supported payment methods (card, Tempo, Link) and URLs for the goal spec, payment link and fulfillment. |
| `/agent.json` | GET | Same as above; duplicate location for convenience. |
| `/goal` | GET | Returns the goal specification describing required input fields (`failing_command_or_request`, `environment`, `exact_error_log`) and expected outputs. |
| `/payment-link` & `/buy` | GET | Returns a Stripe payment link where the agent/user can pay the 500 JPY fee. |
| `/fulfillment` | POST | Submit a JSON payload with your command, environment and error log. After verifying payment, the service returns a JSON object with a diagnosis, likely cause, next commands to run, a retry plan, risk notes and a confidence score. |
| `/llms.txt` | GET | Human‑readable instructions for LLMs and AI agents (do not include secrets). |

### Example Workflow

1. Call `GET /goal` to understand the required input fields.
2. Prompt the user (or automatically) to purchase the service by visiting the URL returned from `GET /payment-link`. The user can pay via Stripe Link agent wallet or card; Link enforces spending limits and human approval when necessary.
3. Once payment is complete, send a `POST` request to `/fulfillment` with a JSON body:

   ```json
   {
     "failing_command_or_request": "python myscript.py",
     "environment": "Ubuntu 20.04, Python 3.10",
     "exact_error_log": "Traceback (most recent call last): ..."
   }
   ```

4. The response will contain the diagnosis, likely cause, suggested next commands, a retry plan, risk notes and a confidence value.

### Technology Notes

* **Tempo** is a payment‑optimized blockchain designed by Stripe. It offers dedicated payment lanes and low fees, enabling stablecoin transactions that cost as little as 0.1 ¢. Tempo’s TIP‑20 token standard allows attachments like invoice IDs, and gas fees can be paid in stablecoins.
* **Machine Payments Protocol (MPP)** is an open standard co‑authored by Stripe and Tempo that allows services to request payment programmatically and return data once payment is confirmed. It supports Shared Payment Tokens (SPTs) so that cards and stablecoins can be used interchangeably.
* **Link agent wallet** extends Stripe’s Link wallet with agent delegation. Users can connect payment methods and grant agents spend limits and approval conditions; sensitive credentials are never shared with the agent.

### Legal and Safety

This service is provided as a digital product. Price is fixed at 500 JPY per diagnostic session.
Due to the nature of digital services, cancellations or refunds are not available once the analysis has begun.
Always sanitize logs before sending them; do not include secrets, credentials or personally identifiable information.

## Limitations
- Phase 1 implementation is rule-based and deterministic.
- Diagnosis is limited to predefined patterns (timeout, permissions, port conflicts, etc.).
- Does not execute any repair commands automatically; provides guidance only.

## Safety Guarantees
- Diagnosis-only public API.
- No remote shell execution.
- No exposure of system secrets.
- Input is treated as data, never executed.

## Intended Usage
- AI agents encountering runtime errors.
- Autonomous coding runtimes needing repair guidance.
- VPS automation systems for crash recovery assistance.

------------------------------------------------------------

## License

MIT
