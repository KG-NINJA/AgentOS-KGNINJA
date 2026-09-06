# Revenue operations: evidence to follow-up work

This implements the observation and internal follow-up portion of the Revenue OS
design inside the existing AgentOS runtime. It closes a specific gap: payment
reports, incomplete releases and bounty observations previously had no shared,
durable next-action queue. It is a local Python standard-library component, not a
deployed Cloudflare Controller or a new authoritative financial ledger.

## Run one complete observation cycle

From the checked-out repository root, with Python 3.11 or later:

```sh
python3 -m factory.revenue.cli collect --run-id review-20260906-01
python3 -m factory.revenue.cli next
python3 -m factory.revenue.cli brief avu_health:COST_BASIS_EXPIRED
```

`bash factory.sh revenue ...` invokes the same implementation. For a machine
consumer, use the direct Python command: Factory's existing shell wrapper prints
diagnostic banners before JSON. There are no new Python dependencies.

The first command reads the bounded host allowlist and persists exact response
bytes, hashes, observation times and failures to
`runtime/revenue/evidence.sqlite3`. The second returns at most five prioritized
actions. The third creates a durable internal brief bound to the exact evidence
and policy hashes. The brief is not a runnable shell job, approval or permission
to make an external commitment. A caller must use the exact task key returned by
`next`; the example key only exists while the corresponding condition is found.

The module is directly callable by an existing authorized runner. No daemon,
Work automation, API credential, service, paid infrastructure or schedule is
installed by these commands. Source collection is an explicit invocation; the
existing scheduled Work task still needs a reachable, installed runtime before
it can invoke it. The report exposes this limitation, rather than inferring
installation from a PR or local test.

## Sources and decisions

The allowlist in `factory/revenue/sources.py` contains the AVU health/stats,
the existing commerce integrity report, the buyer/matcher and model-routing PRs,
the previously contributed bounty PR, and one public bounty repository.
Changing it is a host code review, not an agent-supplied URL.

| Observation | Follow-up |
|---|---|
| Cost basis expired | Read the live row, prepare exact restricted SQL, obtain the required approval, verify recovery |
| Receipts lack captured settlement evidence | Request minimal read-only reconciliation; never recharge the customer |
| PR open or merged | Distinguish implementation, release and independently verified runtime installation |
| Bounty PR merged | Check actual reward terms and payment; neither payment nor a recognized receivable is inferred |
| Bounty open and not assigned to someone else | Prepare research-only review of terms, competition, eligibility and payout |
| Quotes absent | Investigate service availability and one external buyer use case; do not invent demand or self-purchase |
| Read failed, stale or rate limited | Preserve history, make current values unknown and defer further reads |

Issue reads are capped at three pages of 50 and five surfaced candidates. Pull
requests from the issues collection are excluded. Reward labels include the
observed `Stellar Wave` program label, which does not contain the word `bounty`.
Issues that request edits under `contracts/` are excluded from the initial
engineering scope; SDK tests can remain research candidates. This conservative
filter is not a complete security classification or permission to begin coding.
Incomplete pagination stays
explicit and never resolves a previously observed candidate merely because it
fell outside the observed pages. The initial candidate class is **B, research
only**: comments, linked PR competition, residency, AI policy, license, reward
and payout conditions still require a fresh targeted review. No automatic
application, comment, PR publication or engineering job dispatch is present.

HTTP uses GET only, fixed HTTPS origins, no redirects, a bounded body, timeouts,
and at most three concurrent reads. A rate-limit/error response creates a durable
cooldown instead of a busy retry loop. GitHub public reads may optionally use a
host-provisioned `KG_REVENUE_GITHUB_READ_TOKEN`; it is never sent to commerce
origins or written to the evidence database. Give it read-only repository access.
No login, token creation or account change is performed by this module.

## What the financial display means

Counters are **snapshots**, not transactions to add on every poll. Public service
claims are labeled `operator_reported_aggregate`. Imported tool/file captures
are labeled `host_imported_source_claim`, never authenticated settlement proof.
Source generation time and retrieval time are separate. Reading or touching an
old file does not refresh the underlying measurements.

Every observation also retains its capture URL. The policy fingerprint binds
the decision code and source allowlist as well as the control flags. A changed
source URL, missing legacy capture URL or changed rules cannot reuse an old
current-source claim or internal brief silently.

The system keeps service scopes separate. It does not combine USD and USDC,
infer an absent asset ID, treat all paid receipts as settled income, count a
merged bounty PR as cash, or turn a mainnet asset purchase into earned revenue.
Independent counterparty verification, event-level receipt/chain/delivery
reconciliation, refunds, actual costs and profit remain unknown when absent.
Counter decreases create reconciliation tasks. Older values remain explicitly
historical when the latest read fails.

## Connected-tool capture import

When the host cannot make direct HTTP requests, an authorized tool can capture
the same public resource and pass a local manifest to this path. This is a
transport bridge, not a way to turn model-written input into verified evidence:

```json
{
  "schema_version": "revenue-source-capture/0.1",
  "captures": [{
    "source_key": "avu_health",
    "url": "https://agent-economy.kgninja.dev/health",
    "fetched_at": "2026-09-06T13:00:00Z",
    "source_at": "2026-09-06T13:00:00Z",
    "raw_json": "{\"example\":\"replace with the exact captured response bytes\"}"
  }]
}
```

The example body is illustrative and fails the health schema. Preserve the exact
UTF-8 response in `raw_json`, with real capture/source timestamps. A body
`generated_at` or `time` field takes precedence over the supplied source time.
If timing is unknown, use `source_at: null`; the report will refuse freshness.

```sh
python3 -m factory.revenue.cli import-capture /private/capture.json --run-id captured-review-01
```

Identical run replay does not fetch again. Changed plans or imported bytes under
the same run key are rejected. Interrupted runs retain evidence and require a
new explicit run ID. Imports never set a model-supplied role or grant financial
authority. Keep captures and runtime data outside public git history.

## Storage, recovery and rollout

SQLite serializes mutations with `BEGIN IMMEDIATE`; observations, raw snapshots
and prepared briefs are append-only. The database is created with mode 0600 in a
private runtime subdirectory. Existing unrelated databases and symlink paths are
refused. This is an access-controlled local host component, not a security
sandbox against a malicious user who controls that same filesystem.

```sh
python3 -m factory.revenue.cli backup /private/backups/revenue-review.sqlite3
python3 -m factory.revenue.cli stop
```

Backups use SQLite's backup API rather than copying a live WAL database. A
restored copy permits observation/reconciliation and stops new brief preparation.
There is intentionally no agent-facing resume, owner-approval, payment, publish,
arbitrary shell or deploy command. Removing the module and its Factory CLI case
rolls back this code; retain the evidence database. Existing D1, payment flags,
prices, payee, assets, Secrets and schedules are not part of this migration.

To use it continuously, first review/install this commit on the authorized host,
verify its read access, then explicitly connect one existing workflow to the
command and verify one persisted run ID. Scheduled operation, Cloudflare
Workers/D1 deployment, browser owner/agent consoles, authenticated approval APIs,
budgeted job dispatch, fencing, publication and chain-verifying ledger adapters
from the larger Revenue OS design remain separate, unimplemented integration
work. This PR does not claim all 24 design acceptance gates have passed.

## Validation

```sh
python3 -m unittest discover -s factory/revenue/tests -v
bash factory.sh revenue report
```

Tests exercise deduplication, concurrent claims, byte/plan conflicts, interruption,
staleness, changed assignees, partial pagination, evidence retention, rate limits,
exact integer amounts, unknown costs, immutable briefs, untrusted instructions,
unrelated database refusal and stopped backup recovery. They use synthetic
inputs and prove neither live payments nor autonomous customer acquisition.

API references: [GitHub issues](https://docs.github.com/en/rest/issues/issues),
[timeline events for the targeted review](https://docs.github.com/en/rest/issues/timeline),
[Drips contribution, assignment and reward lifecycle](https://docs.drips.network/wave/contributors/solving-issues-and-earning-rewards/).
