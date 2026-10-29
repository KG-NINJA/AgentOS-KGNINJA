# Integration and rollout contract — v1.1

## Implemented boundary

The Python 3.11+ standard-library kernel provides strict finite JSON, explicit
local source manifests, content hashes, bounded read DAGs, SQLite evidence and an
offline paired migration gate. It has no model/API client, financial executor,
arbitrary shell or unrestricted URL fetcher. Application asyncio is not native
GPT-6 async tools. No daemon or ChatGPT account setting is created by this package.

The host must own the adapter registry, policy, root directories and credentials.
A model must never supply Python adapters, operation registration or authority.
Maximum three reads run in flight; only adapter-classified transient reads retry,
at most twice. Permission failure stops queued work and pending retries; already
in-flight reads may complete. Deadlines require cooperative trusted adapters.
Untrusted code needs external process/container isolation, not this library.

## Generator integration

`factory/generator/codex_generate.sh` calls `factory/agent/work_preflight.py` before
its original Codex command. The preflight binds AGENTS.md, config.json and copied
project SPEC.json to content hashes and compares the snapshot to the spec string
in the prompt. Duplicate entity/requirements/quality-policy objects are removed;
the complete original spec stays. Feedback is untrusted data, not authorization.

Evidence stays in runtime/work-platform/evidence.sqlite3: directory mode 0700,
file mode 0600. Only compact receipt metadata is appended to the prompt. Missing
or enabled finance flags, suspected secrets, duplicate JSON keys, unsafe paths,
spec mismatches or stale/conflicting receipts stop with exit 78 before Codex or
fallback. This preflight supports the current all-finance-flags-false state only.

The original gpt-5.3-codex selection, sandbox, approval flags and target constraints
remain unchanged. Earlier scaffolding writes and repair/parser paths are outside
this new boundary. A repository merge is not deployment to a running VPS. Shell
integration tests use a recording Codex stub, not a paid model call.

## Context and evidence

Context manifests select explicit relative .md/.txt/.json paths and optional
SHA-256 content pins. Required files are never truncated. Missing inputs,
symlinks, noncanonical paths, secret-like fields, stale pins or insufficient byte
budget stop compilation. Budgets are UTF-8 bytes, not tokens. The caller must
identify every required source: there is no semantic ranking or automatic source
authority. Secret detection is heuristic, not a guarantee.

For reads, host-created ReadOperation entries validate exact types/resources.
Workers return data, source_refs, observed_at (Unix seconds) and warnings. Source
freshness is the adapter's responsibility; local file read time is not market
observation time. Adapter versions and task arguments bind the run plan. Workers
cannot commit state or receive policy-write, payment, signing or account adapters.

One coordinator atomically claims and finalizes a run. Same-ID/same-plan replay
never calls adapters again; stale cached observations are rejected. Crashes leave
unfinished claims for explicit operator recovery with a new run ID. Raw blobs and
complete run receipts have hashes. Existing unhashed receipts remain unverified
after additive schema migration; do not silently rewrite historical evidence.

Hashes detect local content inconsistency, not hostile owners, provider truth,
signatures, on-chain settlement or independent audit. Roots and database parents
must not be concurrently mutated by hostile local processes. Preserve evidence
through the existing authorized Runbook process; do not invent a new Runbook.

## Validation

From the repository root:

```sh
python3 -m unittest discover -s .agents/skills/gpt6-work-platform/tests -v
python3 -m unittest discover -s factory/agent/tests -p 'test_work_preflight.py' -v
python3 .agents/skills/gpt6-work-platform/scripts/smoke.py
python3 tools/build_work_skill.py --output /new/path/gpt6-work-platform.zip
```

The package is reproducible and includes a SHA-256 manifest. It does not include
the Factory integration or install itself in ChatGPT. Matching main/master and
work/gpt6-* pushes, pull requests and manual dispatch run offline CI. No real
model performance improvement is established by these software tests.

## Model rollout and rollback

runtime-policy.json is candidate intent, not active deployment configuration.
Keep the verified runtime until account/model access and actual response IDs are
checked independently, then freeze at least 30 distinct completed paired tasks
across research, coding, files, tool routing and safety. Match data/tools/budgets,
execution timeouts, effective effort and a campaign-fixed maximum observation gap
for every baseline/candidate pair; record source receipts, prompt/input hashes,
safety, correctness, evidence coverage, latency, input tokens, output tokens and
their non-overlapping total. Use input plus output tokens for the migration gate;
do not treat lower input tokens as efficiency when output growth makes the full
run larger. Record cost only from independently verifiable,
authentication-surface-appropriate per-run billing
evidence; otherwise mark it unavailable and exclude it from improvement eligibility.
Counterbalance baseline-first and candidate-first cases (counts may differ by at
most one), and complete the campaign-selected first side before its mate starts.
The mate-side preflight must re-validate the first side's exact campaign-bound
receipt schema and re-derive its strict completed lifecycle from raw JSONL. A
matching file hash and `completed: true` field alone do not satisfy execution order.
Before mate inference, compare its current Codex CLI version and coarse
authentication surface with the validated first-side receipt. Reject either
mismatch instead of collecting evidence that compilation must later discard.
If the campaign's maximum pair-observation gap has already elapsed since that
first-side receipt, stop before workspace, CLI, authentication or model checks.
Stop at the same boundary when the first-side observation is later than the mate
host's current UTC clock; clock rollback or a future-dated receipt cannot yield a
directionally valid pair and must not consume a second inference.
The remaining observation window must also accommodate the mate's complete frozen
timeout. Check this before runtime inspection and again immediately before claiming
the attempt, so preflight delay cannot spend a call under a shortened or necessarily
invalid comparison condition. Reject the campaign itself when its maximum pair gap
is shorter than its fixed timeout.
Capture the model-process completion time and recheck the directed pair window
before success evidence is written. If the clock moves or the mate completes outside
that window, retain its bounded output as unresolved evidence with the atomic claim;
do not promote it into the campaign or retry it automatically.
Immediately before the final time check and attempt claim, re-open and fully
re-validate the first-side receipt and raw lifecycle. Its receipt hash, event-stream
hash, runtime conditions and observation time must exactly match the snapshot that
authorized the start of mate preflight. Any replacement or invalidation during the
workspace/runtime checks stops before inference.
Atomically create a private `campaign-runtime.json` before the first inference and
bind it to the campaign hash, stable Codex CLI version and coarse authentication
surface. Reject every later case before inference if either runtime condition
changes. If completed legacy evidence predates the lock, derive the initial binding
from all completed receipts and reject an already mixed campaign. A corrupt,
partial or non-private lock requires explicit reconciliation and is never replaced.
Read an existing lock as an exact 0600 regular-file snapshot through a no-follow
descriptor. After validating completed campaign evidence, re-open the lock and
reject changed bytes, identity or mode, including a byte-identical replacement;
never authorize inference from a lock assembled across concurrent snapshots.
Retain that validated snapshot through the remaining predecessor and pair-window
checks, then re-open it once more immediately before the attempt claim. Any lock
replacement in that interval stops before inference rather than consuming a call
under a runtime condition that no longer matches the validated binding.
After the claim is published, re-open the same runtime-lock snapshot again at the
final process-spawn boundary. A replacement during claim publication must start no
model process. Remove only the exact unchanged claim for that unstarted attempt;
if the claim also changed, retain it and fail closed for explicit reconciliation.
Immediately after a completed inference, re-run the stable CLI and coarse
authentication checks and re-read the campaign lock before writing success
evidence. If any of those conditions changed during the call, retain the attempt
claim for reconciliation and do not promote its output into the campaign.
After those runtime and workspace checks, re-open the exact original attempt-claim
snapshot before success promotion. A changed identity, bytes or mode, including a
byte-identical claim replacement, leaves the completed output unresolved with its
claim rather than allowing it to become comparable campaign evidence.
Release a resolved attempt only by re-opening that exact claim snapshot after the
success stream and receipt have been written. If it changed during publication,
leave the claim in place and reject the completed-looking files at mate preflight,
blind grading and compilation; do not unlink the replacement or count the run.
After the exact claim is released, atomically write a 0600 finalization marker
bound to the campaign, case, side, original claim hash and raw event-stream hash.
Every completed-evidence reader must require that marker, re-open its exact private
snapshot after validation and reject a missing or mismatched marker. Thus a crash,
claim deletion or publication failure between receipt creation and finalization
cannot turn incomplete collector state into comparable evidence.
Preserve bounded stdout/stderr in a private per-attempt `unresolved/` directory
with hashes, lifecycle metadata and an explicit non-promotion receipt. Apply the
same handling when the measured checkout changes during the call. Do not release
the claim, automatically retry the case/side or copy response content into public
diagnostics until an operator has reconciled the attempt.
Publish the pre-inference claim atomically as complete private JSON. Bind it to the
campaign, case and side with a cryptographically random private attempt ID, and
bind each unresolved receipt to the exact retained claim SHA-256. A missing,
partial or mismatched claim is not reconcilable evidence and must not be promoted.
Run `factory/agent/gpt6_evaluation.py audit-unresolved` before an operator reviews
any retained attempt. It verifies the 0600 claim and files, 0700 directories,
campaign binding, exact stored hashes and absence of promoted success evidence.
The command is read-only: it does not reveal the private attempt ID or payload,
remove the claim, retry the call or promote output. A failed or ambiguous audit
requires manual investigation; a passing audit still requires an explicit,
separately reviewed reconciliation policy before any state change.
The audit result includes the canonical unresolved-receipt SHA-256. It reads
private files with no-follow descriptors, then re-opens and compares the exact
bytes and file identities after validation. Any concurrent replacement, mode
change, attempt-directory change or success-evidence promotion fails closed so an
operator cannot receive a result assembled from different evidence snapshots.
The audit also fails closed when bounded stdout or stderr was not retained; hashes
copied into a receipt cannot substitute for re-reading the underlying evidence or
re-validating the completed JSONL lifecycle.
The preliminary single-model access probe must likewise re-run the stable CLI and
coarse authentication checks after completion. Treat a mid-probe version or
authentication change as blocked rather than recording successful access. Preserve
bounded stdout/stderr privately with the blocked receipt, including when a completed
response misses the fixed sentinel, while exposing only hashes and lifecycle
metadata. A completed process under changed conditions is not a validated probe.
The mate receipt must bind the exact validated first-side receipt and raw
event-stream hashes. Recompute both before grading and compilation; do not accept
a pair if its predecessor changed after the mate was authorized.
The mate observation timestamp must be equal to or later than the selected
first-side timestamp. Calculate the maximum-gap check in that direction; an
absolute time difference can conceal a pair executed in the wrong order.
The order must match the deterministic schedule derived from the frozen source
commit, case identifiers and prompt hashes; do not hand-pick which model goes first.
Independent grades must name the exact receipt and raw event-stream hashes they
reviewed. Compilation re-derives token counts and completion hashes from that raw
JSONL and rejects evidence changed after grading. A successful receipt also requires
exactly one ordered `thread.started`/`turn.started`/`turn.completed` lifecycle and a
non-whitespace final agent message inside that turn. Missing, reordered or
post-completion messages cannot enter the grading manifest or count toward the
matched-pair minimum. The thread start must be the first stored event and turn
completion the final stored event; prefix or suffix events make the stream
ineligible, while item events remain allowed inside the lifecycle.
Parse JSONL as strict finite JSON: duplicate object keys and `NaN`/infinite values
are malformed evidence, including in fields not otherwise used by the compiler.
Completed receipt and raw JSONL validation uses private no-follow descriptors,
bounded reads and exact file snapshots. Re-open both files after lifecycle and
schema checks and reject changed bytes, identity or mode, including a byte-identical
replacement, so mate preflight, grading preparation and compilation cannot accept
evidence assembled from different filesystem snapshots.
Every case/side must also have a distinct event-stream hash. Reusing one raw JSONL
as more than one execution is ineligible before blind grading and at compilation.
Every case/side must also have a distinct Codex thread ID. Byte-distinct JSONL that
reuses one thread remains one execution and is likewise ineligible at both gates.
Generate a private blind manifest with random sample IDs before grading. It may
contain case context, final response and evidence hashes, but not model, side,
pair order, effort, timing, usage, authentication surface or evidence paths. Keep
the separately generated identity map away from the evaluator. Grade schema v4
uses only sample IDs and binds the grade set to the blind-manifest hash. It does not
accept evaluator-supplied operational cost; compilation
receives the private map and rejects substituted, duplicated or stale evidence.

The offline gate requires no per-case quality regression or safety failure and
at least 10% improvement in a measured operating metric. These are project
migration criteria, not OpenAI requirements or proof of investment performance.
Passing JSON is only eligibility for independent operator review, never proof of
provider authenticity or deployment permission. Do not activate new async,
subagents, steering or cache controls without host support and integration tests.

After independent receipt review and rollback testing, use a controlled read-only
rollout. Never bypass denied access with fallback. Model upgrades do not change
financial authority, frozen strategy versions or elapsed forward-observation
requirements. Keep payment_verified/settled/executed/delivered/outcome_verified
separate. Favorable paper results do not authorize trading.

Roll back the integration commit as a unit. Removing the kernel alone leaves its
generator caller blocked. Preserve SQLite evidence. Real deployment rollback is
host-specific and separate from reverting source or uninstalling a Skill.

## Official sources checked 2026-09-05

- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://help.openai.com/en/articles/20001066-skills-in-chatgpt
- https://learn.chatgpt.com/docs/agent-configuration/agents-md

Product documentation does not establish access in the user's account.
