---
name: gpt6-work-platform
description: Coordinate complex ChatGPT Work or Codex tasks with lean context, bounded read-only workflows, deterministic evidence and GPT-6 migration checks. Use for workflow implementation, orchestration audits, context compilation and model migration; not for simple questions or automatic financial execution.
---

# GPT-6 Work platform

## Scope and activation

This skill helps perform the current authorized task. It cannot change a ChatGPT
account's chosen model, native scheduler, tools, permissions or global runtime.
Installation and successful invocation must be verified separately on each surface.
A file or memory update is not installation. Do not claim persistent behavior from
this skill merely being present in a chat or in a branch.

Read `references/DEPLOYMENT.md` before implementation or rollout and
`references/runtime-policy.json` when choosing a candidate workload profile.
The profile is a proposal, not a declaration that model access is verified.

## Procedure

1. Resolve intent, output, authorized resources and completion checks. Prefer the
   user's current source/version. Keep human input informal; make agent jobs strict.
2. Discover the smallest relevant tool set. Check actual schemas and applicable
   file/project instructions. Use existing direct tools for simple calls.
3. Select required policy, state and evidence. Pin source hashes when versions
   matter. `scripts/work_kernel.py context` can pack a scoped local manifest; it
   stops rather than dropping required context. Its budget is bytes, not tokens.
4. Split independent read-only work from semantic decisions and side effects.
   The Python `run_reads` function accepts host-registered, validated async read
   adapters only: maximum three in flight, two retries for classified transient
   reads, no retries or fallback on permission failures. No financial adapters.
5. Pass each worker a bounded goal, exact inputs, required evidence, deadline,
   call_id and run_id. Workers cannot modify policy or commit state. Use separate
   proposer and critic jobs only when the available runtime supports them and
   independent review adds value; otherwise perform and label a sequential review.
6. Keep raw evidence in the operator-owned store. Return compact source refs,
   hashes, warnings, observation times and failures for synthesis. A coordinator
   alone finalizes the run. Partial or unknown outcomes are never success.
7. Execute consequential actions separately using the existing trusted executor,
   current human authorization and deterministic checks. This kernel cannot send,
   publish, trade, transfer, sign, alter permissions or operate accounts.
8. Validate output and report exact changed locations, tests and unresolved gates.
   Record decisions in the existing authorized Runbook; never invent a new source
   of truth or claim that an unavailable Runbook was updated.

## Model and capability policy

Use GPT-6 Astra as a candidate for judgment-heavy tasks. Do not replace a verified
runtime solely on a model release or this policy. Start migration at the baseline's
reasoning effort. Candidate task-specific efforts are tested separately.

Use `scripts/work_kernel.py evaluate` on at least 30 distinct completed paired
cases with matched inputs, budgets, execution deadlines and a campaign-fixed
maximum observation gap between each baseline/candidate pair, covering research,
coding, files, tool routing and safety. Require no per-case correctness/evidence
regression or safety failure and at least 10% improvement in one measured
operating metric. This is a local migration criterion, not an OpenAI requirement
or financial backtest gate.

Counterbalance which model runs first across the campaign. The baseline-first and
candidate-first counts may differ by at most one, and the selected first side must
complete before its mate starts. Do not let a fixed order or warm-cache effect be
silently attributed to the candidate model.
Before starting the mate, re-validate the selected first side's complete receipt
schema and strict raw JSONL lifecycle, not only its completed flag or file hash.
A forged or incomplete first-side receipt must stop before any second-side model
version, authentication or inference check.
After validating that evidence, require the mate's current Codex CLI version and
coarse authentication surface to exactly match the selected first side before
inference. A condition mismatch is already known to invalidate the pair and must
not consume a second model call.
Also reject the mate before workspace or runtime checks when the campaign-fixed
observation window has already elapsed since the selected first-side receipt. Do
not spend a model call on a pair that cannot satisfy compilation.
Reject a first-side observation that is later than the mate host's current UTC
clock before those checks as well. A clock rollback or future-dated receipt cannot
produce a directionally valid pair and must not consume the second model call.
Require enough time to remain in that observation window for the mate's complete
campaign-fixed timeout. Check this both before runtime checks and immediately before
the attempt claim; preflight delay must not turn an otherwise equal timeout into an
invalid or shorter second-side condition. Reject a campaign whose pair window is
shorter than its fixed timeout because no mate can satisfy that configuration.
After the model process completes, record that completion observation time and
recheck the directed pair window before promoting the output. A wall-clock jump or
completion outside the frozen window leaves private unresolved evidence and the
attempt claim for reconciliation; it must not become comparable success evidence.
Immediately before that final time check and attempt claim, re-open and fully
re-validate the selected first-side receipt and raw lifecycle. Require the exact
receipt hash, event-stream hash, runtime conditions and observation time validated
at the start of preflight; a predecessor replacement during workspace/runtime
checks must stop before the mate model call.
Before any model call, atomically bind the whole campaign to one stable Codex CLI
version and coarse authentication surface. Every later case must match that private,
campaign-hash-bound runtime lock before inference. For legacy evidence without the
lock, recover the binding from every completed receipt and stop on any pre-existing
mixture; never overwrite a corrupt or partial lock without explicit reconciliation.
Open an existing lock through a private no-follow descriptor and retain its exact
bytes, identity and 0600 mode while validating completed campaign evidence. Re-open
it before authorizing inference and reject even a byte-identical replacement or
permission change so one authorization cannot combine two runtime-lock snapshots.
Re-open that same retained snapshot again at the last pre-claim boundary, after
predecessor and time-window revalidation. A lock replacement during those later
checks must stop before the attempt claim and model call.
Because publishing the claim is itself a filesystem operation, re-open the same
runtime-lock snapshot again after claim publication at the final process-spawn
boundary. If it changed, start no model process and release only the exact,
unchanged claim created for that unstarted attempt; a changed claim stays failed
closed for reconciliation.
After every completed model call, re-check the CLI version, authentication surface
and private campaign lock before promoting its output. Re-open the exact original
attempt-claim snapshot after all other post-call checks and before success
promotion. A mid-call update, logout, authentication switch, runtime-lock mutation
or byte-identical claim replacement leaves the attempt unresolved for explicit
reconciliation; it is not comparable success evidence.
Retain bounded stdout/stderr and a private unresolved receipt for that completed
process, including a post-call workspace change. Keep the case/side claim in place,
expose only hashes and lifecycle metadata outside the private store, and never
automatically retry or promote the unresolved output.
Create the claim atomically as complete private JSON before inference, with a
cryptographically random attempt identifier and the campaign/case/side binding.
Bind an unresolved receipt to the exact retained claim hash so reconciliation
cannot attach output to a stale or different claim. Keep the identifier private.
Before any reconciliation decision, use the read-only unresolved-attempt audit to
verify the private claim, receipt, stored stream hashes, modes and non-promotion
state. The audit must retain the claim, expose no attempt ID or payload and refuse
missing, ambiguous, modified or already-promoted attempts. A successful audit is
only evidence integrity; it does not authorize promotion, retry or claim release.
Bind the audit output to the unresolved receipt hash and re-open every claim,
receipt and stored stream through no-follow descriptors after validation. Reject
replacement, permission drift, attempt-set changes or promotion that occurs while
the audit is running; never return a result assembled from mixed snapshots. If the
bounded stdout or stderr was too large to retain, the audit cannot verify its hash
or completed lifecycle and must fail closed instead of trusting receipt metadata.
Apply the same post-call CLI-version and authentication-surface recheck to the
single GPT-6 access probe. A probe that completed across a runtime change is not
valid access evidence even though it is not yet part of a comparison campaign.
Retain its bounded stdout/stderr privately with a blocked receipt, as for other
failed probes; also retain a completed response that does not match the fixed
probe sentinel. Neither condition is validated model access, and private output
must not be copied into the public receipt.
Record the validated first-side receipt and raw event-stream hashes in the mate's
receipt. Recompute that binding before grading and aggregation so later edits to
the predecessor invalidate the pair.
Treat observation time as directional evidence: the mate timestamp must not precede
the campaign-selected first-side timestamp. Compute the pair gap from first side to
mate rather than using an absolute difference that could hide reversed execution.
Derive that order deterministically from the frozen source commit, case identifiers
and prompt hashes. A balanced but manually selected order is not an equivalent
campaign because it can assign favorable order to chosen cases after inspection.

Bind every independent grade to the exact execution receipt and raw event-stream
hash that was reviewed. Re-derive completion and token fields from the raw JSONL
before aggregation; an evaluator reference without those evidence hashes is not a
grade of the stored run. A completed event is not sufficient by itself: require one
ordered lifecycle of `thread.started`, `turn.started`, a non-whitespace final agent
message and `turn.completed` before a run can become success evidence or an
independent grading sample. `thread.started` must be the first stored event and
`turn.completed` the final stored event; reject any prefix, suffix or message outside
that lifecycle while allowing documented item events inside it.
Parse every JSONL event through the same strict finite-JSON boundary used for
receipts. Duplicate object keys or non-finite numbers make the event ambiguous and
must block collection rather than inherit parser-specific meaning.
Open completed receipts and raw JSONL through private no-follow descriptors and
retain their exact bytes, identity and mode while validating them. Re-open both
files before returning the validated result and reject even a byte-identical inode
replacement or permission change; mate authorization, blind grading and compilation
must not consume a mixed snapshot assembled during concurrent replacement.
Require a unique event-stream hash for every case/side execution in the campaign.
The same stored JSONL cannot count as multiple distinct completed runs even when
case-specific receipt fields are rewritten around it.
Also require a unique Codex thread ID for every case/side execution. Reformatting
or padding one valid JSONL stream changes its byte hash but does not make the same
ephemeral thread an independent run; reject that reuse before grading and again
during compilation.

Independent graders may assess safety, correctness and evidence coverage, but must
not supply latency, token or cost measurements. Derive operational metrics only
from execution evidence. Count input plus output tokens for migration efficiency;
an input-token reduction alone is not an improvement if output growth raises total
token volume. Until an authentication-surface-appropriate, independently
verifiable per-run billing record is available, mark cost unavailable and exclude it
from improvement eligibility rather than accepting an estimate or list price.

Blind the independent evaluator to model identity, baseline/candidate side and
execution order. Give the evaluator only randomly identified private grading
samples containing the shared case context, final response and evidence hashes;
hold the sample-to-side map separately for compilation. Bind the returned grades
to the exact blind-manifest hash. Balanced execution order is not evaluator
blinding, and a deterministic sample identifier derived from public case/model
fields is reversible rather than blind.

Do not batch all baseline observations long before all candidate observations.
Reject pairs outside the fixed observation gap and reject unbalanced execution
order so provider load, time drift or cache order cannot be silently presented as
a model-only difference.

Passing supplied JSON only establishes structural eligibility for operator review.
It does not authenticate model IDs, prove capability, install a skill or authorize
deployment. Check provider receipts and real tool integration independently.

Provider-native async tools, dynamic mid-turn effort, subagents and explicit cache
controls are not implemented here. Use them only after the actual host exposes
compatible interfaces and integration tests pass. Application asyncio is different.

## Financial boundary

Preserve existing finance policies, limits, strategy versions and forward-observation
requirements. A model upgrade does not shorten elapsed evidence. Distinguish
payment_verified, settled, executed, delivered and outcome_verified; do not infer
one from another. Local hashes are not signatures, provider testimony is not an
independent audit, and favorable paper results are not trading authorization.
