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

Candidate execution requires stable Codex CLI 0.153.1 or newer. OpenAI added the
GPT-6 Astra model catalog in 0.153.1; older and prerelease CLIs now fail closed
before inference on direct, FIFO and daemon routes. Upgrade and re-run the access
probe rather than interpreting an old-client timeout as a model-access result.
OpenAI released stable CLI 0.154.0 on 2026-09-09 with Astra in the model picker;
prefer that current stable line for a new target-host rollout while retaining
0.153.1 as the explicit-model compatibility floor.

For ChatGPT-authenticated Codex, OpenAI also lists the legacy generator's
`gpt-5.3-codex` as deprecated. This branch does not silently replace that baseline:
first inspect the target host's authentication and effective model, then choose a
supported baseline for the matched campaign. API-key availability is a separate
case and must be verified on the host rather than inferred from this document.
OpenAI's authentication contract also gives ChatGPT, API-key and Codex access-token
sessions different entitlement, billing and administrative scopes. The probe and
collector therefore run `codex login status` and retain only a coarse
`auth_surface`; they never retain its account-oriented stdout or stderr. A compiled
campaign requires one recognized authentication surface across every baseline and
candidate receipt. Unknown or unauthenticated status stops before inference, and
mixed surfaces cannot become a matched comparison. Workload identity requires a
separate reviewed integration because the CLI rejects login management commands
when that environment-controlled method is active.

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

The repository now includes a host-run evidence collector so this gate is not a
manual JSON exercise. It never activates a model and runs one read-only side at a
time. First confirm that the target Codex CLI accepts and completes an explicit
candidate request:

```sh
python3 factory/agent/gpt6_evaluation.py probe --effort <baseline-effective-effort>
```

The probe uses `codex exec --json --ephemeral --ignore-user-config`, a read-only
sandbox, a closed standard-input stream and a fixed no-tool prompt. Closing stdin
is required because Codex otherwise treats a pipe as additional context and a
long-running host may never deliver EOF. A successful result proves that the requested
CLI call completed on that host; because the public JSONL stream does not attest
provider-side model identity, the receipt deliberately keeps
`provider_model_identity_verified: false`. Its private receipt is stored by
default at `runtime/gpt6-evaluation/access-probe.json`.

If the CLI reaches its deadline, exits unsuccessfully, or emits an invalid or
incomplete JSONL stream, the command exits blocked and never counts as a completed
probe or comparison. It preserves stdout and stderr separately up to the existing
16 MiB event limit as private 0600 files and writes a blocked receipt. The
shareable receipt contains
only byte counts, SHA-256 hashes and whether complete JSONL records showed thread
start, turn start, completion or failure, plus the process exit code when known;
it never copies event payloads or stderr content.
This shows whether the CLI emitted lifecycle events before stalling without
turning a requested model name or partial response into access evidence.
An apparently completed stream is still incomplete unless it contains a nonempty
thread ID and a final agent message; those failures use the same private evidence
path and are never promoted to a generic success receipt.

For the comparison, create an operator-reviewed campaign JSON with the exact
`gpt6-evaluation.v5` fields enforced by `validate-campaign`: a baseline model,
`gpt-6-astra`, one shared effort, budget ID, timeout in seconds and maximum paired
observation gap in seconds, a full clean
source commit, and 30
to 1000 distinct cases spanning research, coding, files, tool routing and safety.
Every case also fixes `first_side` to `baseline` or `candidate`; campaign validation
requires those counts to differ by at most one (15/15 for the minimum 30 cases).
The accepted assignment is derived by sorting SHA-256 ranks over the frozen source
commit, case ID and prompt hash, then splitting that ranking between the two sides.
Validation rejects even a balanced hand-picked reassignment, preventing operators
from assigning favorable first/second position to selected cases after inspection.
Keep campaign, grade and evidence files outside the measured checkout. Use a
dedicated detached worktree or clone containing only the selected commit. The
checkout must contain no tracked changes, untracked files or ignored files: Codex
can read ignored local configuration and generated artifacts even though Git does
not include them in the commit.
Run each frozen case twice from that clean commit, in its campaign-selected order.
For a baseline-first case:

```sh
python3 factory/agent/gpt6_evaluation.py collect --campaign campaign.json \
  --case-id <id> --side baseline --workspace <clean-checkout>
python3 factory/agent/gpt6_evaluation.py collect --campaign campaign.json \
  --case-id <id> --side candidate --workspace <clean-checkout>
```

For a candidate-first case, reverse those two commands. The second-side command is
rejected before Codex version, authentication or inference checks unless the exact
first-side receipt and raw event stream already form completed, hash-consistent
evidence for this campaign.

By default, receipts and raw JSONL are written under the collector repository's
ignored `runtime/` storage with directory mode 0700 and file mode 0600. That
collector repository must not also be the measured checkout; pass a separate,
clean checkout through `--workspace`. A command-line timeout that differs from the
campaign is rejected before Codex runs; omitting the flag uses the campaign value.
The collector records requested model/effort, frozen
input and prompt hashes, Codex version, coarse authentication surface, event hash,
latency and token usage. It
does not estimate cost or judge its own output. Model subprocesses receive an
unused pseudo-terminal on stdin because Codex treats every non-terminal stdin,
including `/dev/null`, as additional prompt input; the actual prompt remains the
explicit CLI argument. Each case/side is claimed before
inference: a concurrent or crash-left claim blocks reissue, and completed or
partially written success evidence is never overwritten. Reconcile an uncertain
claim before retrying. Failed attempts are retained in separate private directories
under `blocked/`, so an explicit retry cannot erase the earlier diagnosis.
The checkout is verified again after the model process completes. If its commit or
contents changed during execution, no success receipt is written and the claim
remains unresolved for explicit operator reconciliation.
Receipt schema v6 binds the deterministic campaign order to the prior
authentication, timeout and observation-gap conditions. Retain v1/v2/v3/v4
and v5 receipts as historical evidence rather than rewriting or mixing them into a v6
campaign. Compilation
parses both UTC observation times, rejects pairs outside the frozen gap and reports
the largest observed gap plus baseline-first and candidate-first counts. This
prevents a fixed call order, warm-cache effect or all-baseline-then-all-candidate
batch from silently turning execution conditions into a model-only result.
After all runs complete, create a private evaluator manifest and a separately held
identity map:

```sh
python3 factory/agent/gpt6_evaluation.py prepare-grading \
  --campaign campaign.json --evidence-dir runtime/gpt6-evaluation \
  --output private/blind-manifest.json \
  --mapping-output operator-only/blind-map.json
```

Give the evaluator only `blind-manifest.json`. Its random sample IDs cannot be
recomputed from public campaign fields, and it omits model, baseline/candidate
side, pair order, effort, timing, usage, authentication surface and evidence
paths. Keep `blind-map.json` with the compiler/operator role; do not give it to
the evaluator. Both files are created with mode 0600 and existing outputs are
never overwritten.

Supply a separate `gpt6-evaluation-grades.v3` file keyed only by sample ID, with
safety, correctness, evidence coverage, actual cost, evaluator references, the
reviewed receipt/event-stream SHA-256 values and the exact blind-manifest SHA-256
for all 60 or more runs. Earlier v1/v2 grade files remain historical evidence and
are not silently accepted. Then compile:

```sh
python3 factory/agent/gpt6_evaluation.py compile --campaign campaign.json \
  --evidence-dir runtime/gpt6-evaluation --grades grades.json \
  --blind-manifest private/blind-manifest.json \
  --blind-map operator-only/blind-map.json
```

Compilation rejects missing, extra, mismatched or stale pairs and passes only the
assembled report to the existing migration gate. Completion state, token counts,
thread/final-message hashes and the event hash are re-derived from raw JSONL;
receipt changes after independent grading are rejected through the grade's evidence
hashes. The private map is checked against every current receipt and event stream;
missing, duplicated, substituted or stale samples are rejected. It also rejects a campaign that
mixes Codex CLI versions, even when every individual version supports GPT-6, and
returns the single fixed CLI version, authentication surface and timeout as explicit
comparison conditions. This prevents a client upgrade during collection from
being mistaken for a model-only difference. Even an eligible result remains
operator review material: it cannot activate production or verify provider
authenticity. Do not commit prompts, raw outputs, grades or runtime receipts.

Rollback candidate routing by removing BOTH `FACTORY_CODEX_PROFILE` and
`FACTORY_CODEX_EFFORT` from the operator's service environment, restarting the
affected service, and reconciling already queued candidate requests. Do not
delete receipts or silently reissue uncertain repairs. Revert this integration
as a unit if removing code; its shell callers require the Python helper.

## Official basis, checked 2026-09-22

- https://learn.chatgpt.com/docs/models
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://developers.openai.com/api/docs/guides/latest-model
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://learn.chatgpt.com/docs/auth
- https://learn.chatgpt.com/docs/developer-commands
- https://learn.chatgpt.com/docs/enterprise/access-tokens
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://learn.chatgpt.com/docs/changelog

GPT-6 API function calling requires Responses. This integration uses Codex CLI
and does not assume API parameters can be copied to the app-server protocol.
