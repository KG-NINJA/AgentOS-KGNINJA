# Revenue controller: matching through reconciliation

The controller adds the missing workflow after an internal brief: explicit
executor matching, owner approval, atomic budget reservation, bounded jobs,
independent artifact checks, one-attempt publication and payment reconciliation.
It is a Python 3.11+ sidecar in the existing AgentOS repository, using its own
private SQLite database. It does not deploy the Workers/D1 topology proposed in
the earlier design. Existing observation storage and production D1 are untouched.

Code, protocol tests, installation, account connectivity and actual customer
revenue are separate milestones. The default configuration is stopped, has zero
additional cash allowance, no external sources or publishing targets, and no
payment-execution capability. A synthetic demonstration is not a production run.

## Start here

```sh
python3 -m factory.revenue.cli control capabilities
python3 -m factory.revenue.cli control demo
python3 -m unittest discover -s factory/revenue/tests -v
python3 -m factory.revenue.control.isolation_acceptance
```

The demo exercises observation, proposal, approval, reservation, claim, result,
verification, actual-cost recording, incoming-payment reconciliation and
acceptance using **synthetic adapters**. It creates no paid API calls, posts or
revenue. The isolation acceptance command is a different test: real Docker
containers must reject host-secret access, networking, a regressing patch and
replacement of protected baseline tests. Missing Docker is a failure, not a
passing skip. CI runs this gate on Ubuntu 24.04 without pulling an image: an empty
local image is created, then only the system runtime and prepared inputs are
mounted read-only. No host home, control database, credential, SSH socket or
Docker socket is mounted into the container.

The trusted harness owns the prepared project and test files. It has only the
two identity-switching capabilities needed to start project checks under UID
65534; project processes lose those capabilities. They cannot rewrite baseline
tests during execution, replace the harness's log file or signal the harness.
Only scratch under `/tmp` is writable by project checks. Builds that require
writing into the project tree need a separately reviewed profile.

For an authorized host installation, copy the example config to a private path,
configure only reviewed capabilities, and provision separate credentials. Keep
the config and credential files mode 0600; each actor has exactly one role and a
different high-entropy token. The credential file contains SHA-256 token hashes,
not raw tokens:

```json
[
  {
    "actor_id": "owner-local",
    "role": "owner_approver",
    "token_sha256": "REPLACE_WITH_SHA256_OF_A_HOST_GENERATED_HIGH_ENTROPY_TOKEN",
    "expires_at": 0
  }
]
```

This deliberately invalid template must be replaced with a real hash and future
Unix expiry on the owner's machine. Do not paste credentials into chats, source
issues, runner input, source control or the Work agent's browser. Provision the
owner credential independently of every agent credential.

```sh
python3 -m factory.revenue.cli control serve \
  --config /private/revenue/controller.json \
  --db /private/revenue/controller.sqlite3
```

Open `http://127.0.0.1:8789/owner` on the owner's machine for approval and budget
management. `/agent` shows evidence and work state. Tokens remain in page memory;
there are no cookies or browser storage. The HTTP listener is deliberately
loopback-only, rejects other Host/Origin values and has no CORS. For a remote
host, use an owner-controlled SSH tunnel once SSH is working; do not expose this
HTTP port directly. This PR installs no service, login, tunnel or schedule.

Before starting, run `python3 -m factory.revenue.cli control doctor --config
/private/revenue/controller.json` (as one command). The JSON report checks private
configuration, policy, unexpired role credentials, source/target allowlist
consistency, configured verifier/payment adapters, Docker availability and the
Codex executable. Exit 78 means local prerequisites are missing. It uses a
disposable database; it never opens the production ledger, prints credentials,
logs in, calls a paid model, or probes payment RPCs. Passing local checks does
not establish live connectivity, billing limits, isolation acceptance, deployment
or revenue; these remain explicit checks in the report. A separately integrated
non-Codex runner requires its own reviewed acceptance checks.

`serve` now expires abandoned jobs before opening the listener and every 30
seconds while serving. The host maintenance identity can only run the existing
expiry operation. It advances fences, marks costs unknown, and stops new work;
it cannot approve work, publish, pay, release a reservation or restart a job.
Idle sweeps create no audit/idempotency rows. Expiry still runs while stopped.
Initialization occurs before request threads to avoid first-start database races.
Maintenance failure terminates the serving loop; the CLI closes the listener.
Already accepted requests may finish; an in-flight external effect cannot be
undone. Runner processes must still enforce their own deadlines and lease loss.

On the authorized persistent host: run `doctor`, complete the actual isolation
acceptance test, then supervise the documented `serve` command using the existing
service manager. Verify authenticated `/api/summary` before owner resume. To stop,
use the authenticated `stop` operation, terminate active executors on lease loss,
reconcile costs/effects, then stop the serving process. Use the backup command
below before replacing a release. Keep the prior code/configuration available;
restoring a backup always requires stopping the old instance first. This change
does not install a service manager or an authenticated coding runner.

## Human approval and machine contracts

Human goals can remain informal. Collectors and runners must send strict JSON.
All API mutations require an authenticated bearer identity, schema version
`revenue-controller/0.2`, an idempotency key and a payload. A changed payload under
the same actor/operation/key returns 409. The server never trusts a submitted
role, source instruction saying “approved”, runner test status or payment flag.

```json
{
  "schema_version": "revenue-controller/0.2",
  "idempotency_key": "unique-stable-key-for-this-exact-request",
  "payload": {}
}
```

| Endpoint after `/api/` | Role | Effect |
|---|---|---|
| GET `identity`, `policy`, `summary`, `opportunities`, `proposals` | authenticated | Read bounded state and exact proposal bindings |
| POST `observe` | collector | Append schema-valid observation and exact source bytes |
| POST `match`, `propose`, `admit` | agent_operator | Match explicit engineering capabilities; propose work; reserve an approved job |
| POST `approve`, `revoke`, `budget`, `resume` | owner_approver | Bind exact authority, limits, evidence and expiry |
| POST `cancel-job` | owner_approver | Cancel a pending/running job, advance its fence, require cost reconciliation |
| POST `claim`, `heartbeat`, `result` | runner | Claim one bounded job, maintain lease, submit untrusted artifacts |
| POST `verify` | verifier | Run host-pinned checks through the isolation adapter |
| POST `execute` | publisher | One owner-approved comment or Draft PR attempt |
| POST `reconcile-effect`, `reconcile-payment` | reconciler | Read external evidence and deduplicate existing effects/payments |
| POST `delivery`, `adjustment`, `cost` | reconciler or owner | Append reviewed acceptance, adjustments and actual costs |
| POST `stop`, `expire` | safety_monitor or owner | Stop new commitments; expire lost runner claims |
| POST `capability-failure` | collector, runner, agent_operator, safety_monitor | Persist login/tool/isolation/rate-limit failure and stop |

`approve` takes the complete `bindings` object returned by `propose`, an expiry
within 15 minutes, and an evidence-review reference. The binding covers proposal
ID, exact payload bytes/hash, source observation hash, code/config/schema policy
hash, destination and all three caps. Revocation and consumption are enforced by
the server. The owner screen shows the exact proposal before its approval button.
An approved proposal does not itself execute anything. Changed source or policy
requires a new proposal and approval.

`observe` takes `event_key`, `opportunity` (bundled opportunity schema) and
`source_utf8`. The source bytes must hash to the schema's snapshot hash, and the
source must be host allowlisted. Observations are claims reviewed by a separate
owner, not authenticated truths merely because they validate. Open bounties with
unknown eligibility, terms, competition, assignment or cost do not become jobs.
Reward/probability hypotheses do not grant authority or count as receivables.

`match` takes `languages`, `max_cash_microusd`, `max_work_minutes`,
`max_human_minutes`, `max_results` (1–5). It ranks eligible engineering demand by
bounded cost and time, with explicit rejected reasons. This is distinct from
the existing HyperXosist commerce matchmaker, which matches a buyer's stated need
to the operator's six reviewed API capabilities. Neither path invents an
external buyer, automatically signs a purchase or charges a brokerage fee.

An engineering `propose` payload has `opportunity_id`, `kind: "engineering"`,
`caps: {cash,work,human}` and `action` containing `repo`, `base_commit`,
`objective`, `allowed_paths`, `denied_paths`, `checks_profile`. Paths are canonical
relative paths without trailing slashes, wildcards or traversal. Cash is
microUSD; work and human time are minutes. Source estimates and a reviewed cost
basis are required. Unknown included allowance is not an assumed zero cost.

`admit` takes `proposal_id`, `approval_id`, `budget_id`. `BEGIN IMMEDIATE` serializes
the conditional budget update, reservation and job insert. A zero-row update
inserts nothing. Non-overlapping budget periods are at most 24 hours, bounded by
host policy. There is one engineering slot, one execution attempt, a 60-second
heartbeat convention and a lease of at most 15 minutes bounded by job expiry.
Old fencing tokens cannot submit results. The host runner must physically stop
on lease loss or timeout; SQLite fencing alone does not kill an external process.

## Connect an existing coding runner

The supplied client uses only loopback HTTP, refuses redirects/proxies and reads
a role-specific `KG_REVENUE_<ROLE>_TOKEN` from the authorized host environment.
It does not print the token or propagate it into a job. Owner tokens must never
be available to the runner environment.

```sh
python3 -m factory.revenue.cli control invoke observe --role collector \
  --payload /private/observation-payload.json --key observed-source-01
python3 -m factory.revenue.cli control export-job JOB_ID \
  --output /private/jobs/job-package.json --key claim-job-01
python3 -m factory.revenue.cli control import-result \
  --job-package /private/jobs/job-package.json \
  --result /private/jobs/result.json --artifact /private/jobs/artifact.json \
  --key completed-job-01
python3 -m factory.revenue.cli control invoke verify --role verifier \
  --payload /private/verify-job-payload.json --key verify-job-01
```

The exported package contains the strict job plus fence and lease. Import checks
job identity, pinned commit and canonical artifact hash before the server checks
the live lease. The artifact is `{"files":{"src/file.py":"exact UTF-8 content"}}`;
it cannot delete files, create symlinks, change protected paths or submit arbitrary
host paths. The result follows the bundled execution-result schema. Its claimed
PASS and cost remain untrusted. The server moves it to verification pending and
cost unknown. Actual costs can be finalized only after the entire job, including
verification, is terminal.

Automatic Codex invocation is **not installed by this bridge**. A compatible
authenticated, budgeted, isolated coding runner/model proxy must already exist
on the authorized host. This environment has no Codex binary/login or supported
local Docker runtime; calling ordinary host shell code instead would violate the
execution boundary. Source generation and live model billing are not verified
by the synthetic demo. The job protocol and import/export commands are the
concrete handoff for that existing executor, not a claim of one being installed.

## Source refresh, verification and publication adapters

Host `source_adapters` map each allowed opportunity URL to `urls` (1–5 fixed
read-only JSON URLs) and optional `token_env`. `SnapshotSource.read()` returns
canonical UTF-8 bytes for the complete configured response list. Preserve those
exact bytes in `source_utf8`. Include assignment, relevant comments/timeline,
terms and eligibility evidence when they affect a decision. Host review must
establish pagination completeness; three fetched pages do not prove an entire
large discussion was reviewed. New source content blocks admission/publication
until it is re-observed and re-approved. Refresh occurs immediately before the
transaction and must remain within 60 seconds. Missing or failed refresh blocks.

Each `verifier_profiles` entry contains `repo_url`, pinned `commit`, local
`repo_path`, `snapshot_sha256`, `protected_paths`, and `checks` (argument arrays,
never shell strings). `control snapshot-hash REPO COMMIT` computes the hash of a
tracked committed text snapshot without checkout, hooks or network access.
Profiles currently accept bounded text-source repositories; binary files,
symlinks and missing dependencies fail closed. Prepare dependencies and a
compatible host runtime explicitly before enabling a real project's profile.
Protected baseline checks cannot be overwritten by the artifact. Container
limits cover network, mount access, capabilities, memory, processes, output and
time. The verifier's code/config fingerprint is bound into the approval policy.
Tests are evidence of the checked behavior, not a proof of arbitrary program
correctness or an independent audit of a vendor.

For GitHub publication, configure `publisher` with allowlisted API `targets`,
`token_env`, authenticated GitHub `login` and `verified_heads`. Enable
`allow_publication` only for the authorized installation. An `issue_comment`
action contains exact `target`, `body_utf8` and a unique `reconciliation_tag`
(`kg-revenue:` followed by 32 lowercase hex digits), included in the approved
body. A `draft_pr` additionally needs `title`, `head`, `base`, `job_id`,
`artifact_sha256`. Its already uploaded head must be host-bound to the verified
artifact, base, `commit_url` and `commit_sha`; the adapter rechecks that commit.
Uploading Git objects/branches is a separate pre-existing authorized executor
operation. This adapter does not hide those extra side effects in “create PR”.

The controller commits SENDING and consumes approval before sending. A lost
response or persistence failure stays UNKNOWN/SENDING and is never auto-retried.
Reconciliation compares exact approved text, marker, author, and PR head; absence
or ambiguous results remain unknown. It can read at most three 100-entry pages.
A confirmed send also requires reviewed actual costs before the next commitment.
Once a network request is in flight, stopping the controller cannot undo it.

## Financial reconciliation

`payments` contains fixed `networks`, reviewed counterparty `relationships`, and
`allocations` connecting a specific transfer identity to an engagement and review
reference. Each network needs `rpc_url`, lowercase `recipient`, allowlisted
lowercase ERC20 `assets`, minimum `confirmations`, and `synthetic`. No signing key
or transaction send method exists. The reconciler checks RPC chain, successful
receipt, canonical block, confirmations, Transfer event, asset, recipient, and
log index. The proof is labeled host RPC testimony. Mainnet alone does not prove
third-party revenue; an unknown counterparty remains unknown and excluded.

Reconcile input is `opportunity_id`, integer `chain_id`, `tx_hash`, integer
`log_index`. The unique identity is chain + transaction + log index. A host-reviewed
receipt-to-engagement allocation is required: arbitrary incoming money cannot
be associated with any desired order by the agent. Replaying after a DB failure
re-reads the same identity and never recharges a customer. Existing x402 payment
execution remains a different deployed service and is not changed here.

The ledger separates received cash, matched acceptance, deferred cash,
unreceived accepted amounts and excluded self/internal/synthetic transfers.
An accepted/merged PR is not cash. One acceptance per engagement/asset prevents
polling from creating repeated receivables. `delivery` and `adjustment` evidence
is explicitly an authenticated reviewer attestation; the code does not certify
the truth of an arbitrary evidence URL. Refunds, disputes and reorgs append
bounded adjustment events linked to the original transfer. A dispute reduces
available recognized amounts without rewriting the original receipt or sending
money. Bank/payout movements between owned accounts are INTERNAL, not new sales.
Actual cost allocation is once per reservation, retains its evidence, and records
overruns before stopping. Profit stays unknown without audited FX and full cost
allocation. Native assets are never added together or treated as USD implicitly.

## Stop, restore and remaining production gates

`stop` blocks new proposals/admissions/sends/verifier starts and runner heartbeats; active host
executors must honor lease loss. Reconciliation remains available. Only the owner
can resume after current policy review, with no unknown costs/effects or active
budget overrun. The serving process invokes `expire` at startup and every 30
seconds; direct engine embeddings must arrange their own expiry cadence. No
external monitoring daemon is silently registered by this PR.

Unknown cost by itself blocks new commitments while an already approved verifier
finishes within the job deadline. An explicit stop also blocks that verifier.
The owner can cancel the job with `job_id` and `review_ref`, reconcile costs and
then resume. Queued and verification-pending jobs expire too, preventing an
abandoned job from holding the single execution slot indefinitely.

```sh
python3 -m factory.revenue.cli control backup \
  --db /private/revenue/controller.sqlite3 \
  /private/backups/controller-review.sqlite3
```

Backups are stopped copies. In-flight effects become UNKNOWN; queued/running/
verifying jobs are cancelled with advanced fences and unknown cost. Before
restoring, stop the old controller/runner instance and reconcile anything that
occurred after the backup. A local backup cannot discover effects absent from its
snapshot or safely operate as a second live controller. Never run two restored
copies as independent authorities. Source/effect/payment/audit histories are
append-only; SQLite's transactional backup includes the WAL safely.

The remaining live gates are explicit: authorized host deployment and credentials,
compatible isolated coding executor, current eligible assigned work or a real
external buyer, verified source/payment mappings, and production service recovery.
The separate production D1 cost-basis update still requires reading the live row,
showing its exact restricted SQL and the required approval. No Worker redeploy,
Secret/price/payee/network/asset change, actual purchase, Registry registration,
`production:enable`, trading switch or main-branch merge is performed by this
controller change.

## Acceptance evidence

`factory/revenue/tests/test_controller.py` maps `test_p01`–`test_p24` to the earlier
24 P0 requirements. Additional tests cover HTTP authorization, same-key concurrent
sends, ERC20 finality/identity/allocation, source changes, duplicate costs, restore,
and explicit capability matching. `revenue-isolation` in CI is the separate actual
sandbox check for P08/P21. All test funds, users, destinations and events are
fixtures. Passing these tests does not establish live revenue or account access.

Primary references: [GitHub issue comments](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment),
[issue timeline](https://docs.github.com/en/rest/issues/timeline),
[ERC20 Transfer events](https://eips.ethereum.org/EIPS/eip-20),
[Docker runtime options](https://docs.docker.com/engine/containers/run/).
