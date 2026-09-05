# GPT-6 runtime integration

Status: implemented and tested offline; candidate routing is opt-in. This is not
a model-access receipt, a performance benchmark, or a VPS deployment.

## Problem and implementation

The generator pinned gpt-5.3-codex while interpretation and three repair routes
inherited machine configuration. Merely editing the Work platform candidate
policy could not switch those actual calls. `factory/agent/codex_runtime.py` now
owns the CLI arguments for generation, interpretation, direct repair, FIFO
app-server repair and queued daemon repair. It uses the existing Codex CLI auth
path and adds no OpenAI API client or credentials.

The default `legacy` profile preserves the generator model pin and the inherited
repair/parser settings. `gpt6` explicitly selects `gpt-6-astra` for all five
routes. The operator must supply `FACTORY_CODEX_EFFORT` as low, medium, high,
xhigh or max. Unknown, missing and unsupported effort values fail closed. This
prevents an implicit none/minimal effort or an arbitrary new default from being
mistaken for a fair baseline comparison. Read the actual baseline effective effort
from the deployment before the first paired run.

Inspect without starting Codex:

```sh
FACTORY_CODEX_PROFILE=gpt6 FACTORY_CODEX_EFFORT=high \
  python3 factory/agent/codex_runtime.py --role generation --inspect
```

`high` above is an example, not a measured optimum. Inspection explicitly returns
`model_access_verified: false`. Environment values belong to the trusted host;
never copy them from a task, generated file, spec, feedback or queue payload.

Candidate generation failures do not fall back to successful local scaffolding.
Candidate interpretation failures cannot become successful heuristic output.
Candidate repair failures/timeouts cannot retry through another backend. Daemon
queue requests bind profile/model/effort and must match the daemon's own selection;
the request cannot override it. Align and restart the daemon during a separately
reviewed rollout. Do not run an old daemon against a newly switched producer.

The approval/sandbox arguments, finance switches, queue leasing and existing
local evidence preflight are preserved. Deterministic functions and the separate
Luna workload are not relabeled or expanded. The legacy temperature-sweep API
experiment is outside this CLI route: GPT-6 does not accept its sampling controls.
Keep it on its existing experimental baseline until a separately designed study
is ready. No sweep or paid inference was executed for this change.

## Validation and rollout

```sh
python3 -m unittest discover -s factory/agent/tests -p 'test_codex_runtime.py' -v
python3 -m unittest discover -s factory/agent/tests -p 'test_work_preflight.py' -v
```

Tests use recording Codex stubs and mocked app-server RPC. They verify command
delivery and rejection, not actual provider acceptance. Existing Work platform
tests and repository selftest remain required. CI watches every new call path.

Before production activation, follow the existing Work platform rollout contract:
verify Codex version/account/model access, match effective effort and inputs for
at least 30 paired cases, inspect provider receipts, assess safety and quality,
then perform a controlled rollout with rollback. A JSON gate is not independent
evidence. No default production model is changed by this branch.

Rollback candidate routing by removing BOTH `FACTORY_CODEX_PROFILE` and
`FACTORY_CODEX_EFFORT` from the operator's service environment, restarting the
affected service, and reconciling already queued candidate requests. Do not
delete receipts or silently reissue uncertain repairs. Revert this integration
as a unit if removing code; its shell callers require the Python helper.

## Official basis, checked 2026-09-05

- https://learn.chatgpt.com/docs/models
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://developers.openai.com/api/docs/guides/latest-model

GPT-6 API function calling requires Responses. This integration uses Codex CLI
and does not assume API parameters can be copied to the app-server protocol.
