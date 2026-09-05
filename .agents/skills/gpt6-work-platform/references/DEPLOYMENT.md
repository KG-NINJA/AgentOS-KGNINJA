# Integration and rollout contract

## What exists

`work_kernel.py` is Python 3.11+ standard-library code. It implements a strict JSON
boundary, local content-pinned context packing, a bounded read DAG, SQLite atomic
run claims/finalization, content-addressed raw evidence and an offline paired gate.
It makes no OpenAI, Coinbase, x402 or other network request. No API key is needed.
It does not run a daemon or change any production process.

`runtime-policy.json` documents candidate profiles and default limits. The Python
runtime enforces its own hard limits. Merely changing the JSON does not configure
ChatGPT, override a Python limit or activate a model. `route` returns candidate
intent only. No synthetic test output is a real provider response.

## Local validation

From this skill directory:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/smoke.py
python3 scripts/work_kernel.py route --profile references/runtime-policy.json --workload architecture
```

Context manifest example, stored separately from credentials:

```json
[{"path":"AGENTS.md","required":true},{"path":"CURRENT_STATE.md","required":true}]
```

```sh
python3 scripts/work_kernel.py context --root /operator/owned/runbook --manifest manifest.json --budget-bytes 24000
```

Sources must be explicit relative `.md`, `.txt` or `.json` paths. Required files
are never truncated. A missing file, symlink, stale content pin, suspected secret
or insufficient budget blocks compilation. There is no recursive file discovery,
semantic relevance ranking, tokenizer accounting or automatic source authority.
The caller is responsible for identifying all required policies and evidence.

## Integrating actual reads

A trusted host creates `ReadOperation(version, validate, async_call)` entries and
an exact operation allowlist, then calls `run_reads` with a unique run ID and a
private `EvidenceStore`. Workers return exactly `data`, `source_refs`,
`observed_at` (Unix seconds) and `warnings`. Adapter versions participate in plan
identity. Adapter validation must check exact operation-specific types, approved
resources and source freshness; a connector's display name is not authorization.

Use separate least-privilege read credentials. Never give workers write adapters,
policy/state handles, payment keys, shell access or an unrestricted HTTP client.
Do not trust model-created functions or claimed `effect=read` metadata. Adapter
functions and registry construction are operator-trusted code, not model output.

`asyncio` deadlines are cooperative: an adapter that blocks the event loop or
suppresses cancellation can defeat them. Use process/container isolation for
untrusted code; this library is not that isolation layer. Roots, database paths,
and their parent directories must be operator-owned and not concurrently mutated
by hostile local processes. Secret detection is heuristic, not a guarantee.

The coordinator writes after each complete DAG run. A crash/cancellation leaves
an unfinished claim; this is intentionally not automatically replayed. Preserve
it for audit and recover explicitly with a new run ID. Do not delete evidence to
hide failure. Same-ID, same-plan replay does not call adapters again, and a stale
cached observation is rejected. Use a new run ID for a fresh observation.

Hashes demonstrate local content consistency, not signatures, tamper-proof storage
against its owner, source truth, on-chain settlement or third-party verification.
Archive/export decisions through the existing authorized Runbook process.

## Promotion and rollback

1. Locate the real runtime and baseline model/effort; record a restorable config
   snapshot without secrets. Do not create a replacement Runbook if its real
   repository is inaccessible.
2. Confirm account/model access using the authorized host. Independently validate
   actual response model IDs and any needed tool features. No credential creation
   or paid inference is authorized by importing this package.
3. Freeze at least 30 distinct paired tasks and input hashes. Use the same tools,
   data and budgets and preserve baseline effort. Record provider response refs,
   model, effort, prompt hash, completion, safety, correctness, evidence coverage,
   latency, input tokens and cost. No comparison has been run by this package.
4. Run the offline gate; review the raw receipts independently. An eligible JSON
   report never auto-activates. Test deployment bindings and rollback explicitly.
5. Start a controlled read-only rollout. Restore the prior verified configuration
   on quality/safety/integration regression. Never use fallback to bypass denied
   access. Only after evidence supports it, test workload-specific effort changes.

A safe rollback for this additive change is to uninstall this skill or revert its
commit. Existing Factory code, queues, finance flags and deployed runtime were not
modified. Reverting the skill does not erase generated evidence. Restoring a real
model deployment requires its own tested, host-specific configuration rollback.

## Sources checked 2026-09-05

- OpenAI model card: https://developers.openai.com/api/docs/models/gpt-6-astra
- OpenAI model guidance: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra
- OpenAI Skills help: https://help.openai.com/en/articles/20001066-skills-in-chatgpt

Documentation establishes product guidance, not access in the user's account.
