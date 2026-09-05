# GPT-6 Work platform integration v1.1

Status: **generator preflight integrated in source and tested offline; real GPT-6
routing, ChatGPT Skill registration and external host rollout are not activated**.

## Active source call path

`factory/generator/codex_generate.sh` calls `factory/agent/work_preflight.py` before
its existing Codex invocation. The preflight uses the Work kernel's bounded reads,
strict JSON and SQLite receipts against three explicit local sources: AGENTS.md,
config.json and the copied project SPEC.json. The snapshot must match the original
spec string in the generation prompt. Duplicate object copies were removed from
the prompt; the original complete spec is retained.

A rejected preflight exits 78 and never reaches the model call or fallback. The
actual generator is tested with a recording Codex stub, including rejection cases.
No real provider request is made by those tests. The existing gpt-5.3-codex model,
approval and sandbox flags have not been changed. Repair/parser paths have not yet
been integrated with this preflight and must not be reported as covered.

## Evidence and safety

Evidence stays under runtime/work-platform (0700), in evidence.sqlite3 (0600).
No spec/account data is committed by the code or printed to the model as raw
preflight output. Raw source snapshots remain in the local store. Secret detection
is heuristic; do not place credentials in project specs or instructions.

The kernel now rejects duplicate keys in scoped JSON files, rejects noncanonical
source-path aliases, hashes complete receipts, binds finalization to a claimed run,
and cancels pending retries after a permission failure. Existing unhashed receipts
remain unverified after the additive SQLite schema migration; use a new run ID for
new observations, never silently rewrite historical evidence.

## Validation and packaging

```sh
python3 -m unittest discover -s .agents/skills/gpt6-work-platform/tests -v
python3 -m unittest discover -s factory/agent/tests -p 'test_work_preflight.py' -v
python3 .agents/skills/gpt6-work-platform/scripts/smoke.py
python3 tools/build_work_skill.py --output /new/path/gpt6-work-platform.zip
```

CI runs the tests for matching changes on main/master and work/gpt6-* branches,
for pull requests and on manual workflow dispatch. Packaging uses fixed timestamps
and an embedded SHA-256 manifest; existing output files are never overwritten.
The package contains the reusable Skill, not the Factory generator integration.

## Unchanged external boundaries

A repository merge or successful CI is not a VPS deployment, account-wide Skill
installation, provider access probe, real model comparison or financial authority.
The Factory queue, 50-worker Swarm and finance flags remain unchanged. The existing
generator must be updated on its actual host before that host runs this preflight.
ChatGPT Skills require their own supported installation surface and permission.

Read `.agents/skills/gpt6-work-platform/references/DEPLOYMENT.md` for exact trust
assumptions and migration/rollback gates. Roll back the integration commit as a
unit; removing only the kernel leaves the generator intentionally blocked. Keep
historical evidence. No new approval, financial authority or external credentials
are created by this implementation.

## Actual Codex call routing

The opt-in candidate route now reaches generator, interpreter and all three
repair paths through a shared CLI helper. See [runtime routing](gpt6-runtime-routing.md)
for exact activation state, offline checks, failure semantics and rollback.
The separate Skill runtime-policy.json remains candidate intent.
