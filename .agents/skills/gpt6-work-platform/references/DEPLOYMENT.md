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
across research, coding, files, tool routing and safety. Match data/tools/budgets
and effective effort; record source receipts, prompt/input hashes, safety,
correctness, evidence coverage, latency, input tokens and cost.

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
