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
cases with matched inputs and budgets, covering research, coding, files, tool
routing and safety. Require no per-case correctness/evidence regression or safety
failure and at least 10% improvement in one measured operating metric. This is a
local migration criterion, not an OpenAI requirement or financial backtest gate.

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
