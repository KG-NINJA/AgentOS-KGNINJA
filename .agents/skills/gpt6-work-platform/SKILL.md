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
Before any model call, atomically bind the whole campaign to one stable Codex CLI
version and coarse authentication surface. Every later case must match that private,
campaign-hash-bound runtime lock before inference. For legacy evidence without the
lock, recover the binding from every completed receipt and stop on any pre-existing
mixture; never overwrite a corrupt or partial lock without explicit reconciliation.
After every completed model call, re-check the CLI version, authentication surface
and private campaign lock before promoting its output. A mid-call update, logout,
authentication switch or lock mutation leaves the attempt unresolved for explicit
reconciliation; it is not comparable success evidence.
Apply the same post-call CLI-version and authentication-surface recheck to the
single GPT-6 access probe. A probe that completed across a runtime change is not
valid access evidence even though it is not yet part of a comparison campaign.
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
