"""Deterministic projections, not model judgement or settlement verification."""
import re
from .sources import RevenueError, SOURCES, digest, instant, json_bytes, strict_json

POLICY = {
    "schema_version": "revenue-operations/0.1", "mode": "observe",
    "freshness_seconds": 900, "max_parallel_reads": 3, "max_candidates": 5,
    "external_writes": False, "payments": False, "model_dispatch": False,
    "production_changes": False, "additional_spend_cap_microusd": 0,
    "candidate_owner": "KG-NINJA",
}
POLICY_HASH = digest(json_bytes(POLICY))


def integer(value):
    if type(value) is not int or value < 0:
        raise RevenueError("INVALID_COUNT")
    return value


def atoms(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,77}", value):
        raise RevenueError("INVALID_ATOMS")
    return value


def finding(code, priority, action, subject="", **details):
    return {"code": code, "priority": priority, "action": action, "subject": str(subject),
            "details": details, "external_execution_authorized": False}


def project(source_key, observation, now):
    source = SOURCES[source_key]
    result = {"source_key": source_key, "url": source.url, "fresh": False,
              "evidence_level": "github_provider" if source.url.startswith("https://api.github.com/")
              else "operator_reported_aggregate", "metrics": None, "findings": [],
              "snapshot_sha256": observation["snapshot_sha256"],
              "source_at": observation["source_at"], "fetched_at": observation["fetched_at"]}
    result["capture_method"] = observation.get("capture_method", "public_get")
    if result["capture_method"] == "host_import":
        result["evidence_level"] = "host_imported_source_claim"
    if not observation["ok"]:
        result["findings"] = [finding(observation["error"], 5, "Restore read access; retain last evidence as historical.")]
        return result
    times = [observation["fetched_at"], observation["source_at"]]
    if any(t is None for t in times) or any(not -60 <= now - instant(t) <= POLICY["freshness_seconds"] for t in times):
        result["findings"] = [finding("SOURCE_STALE", 5, "Read the source again before acting; do not refresh old values by changing file time.")]
        return result
    data = strict_json(observation["raw"])
    try:
        metrics, findings = normalize(source.kind, data)
    except (KeyError, TypeError, AttributeError, RevenueError, ValueError):
        result["findings"] = [finding("SOURCE_SCHEMA_CHANGED", 5, "Review the source contract before using its numbers.")]
        return result
    result.update(fresh=True, metrics=metrics, findings=findings)
    return result


def normalize(kind, data):
    findings = []
    if kind == "avu_health":
        checks = data["checks"]
        if data["service"] != "agent-verification-utility" or any(type(checks[k]) is not bool for k in
                ("cost_basis_fresh", "deploy_enabled", "runtime_enabled", "payments_enabled")):
            raise RevenueError("INVALID_HEALTH")
        if not checks["cost_basis_fresh"]:
            findings.append(finding("COST_BASIS_EXPIRED", 10,
                "Read runtime_controls control_id=1, show the exact three-column SQL, obtain approval, then recheck availability."))
        if not all(checks[k] for k in ("deploy_enabled", "runtime_enabled", "payments_enabled")):
            findings.append(finding("SERVICE_DISABLED", 10, "Review the disabled control and its authorization; do not enable automatically."))
        return {"status": data["status"], **checks}, findings
    if kind == "avu_stats":
        if data["service"] != "agent-verification-utility":
            raise RevenueError("WRONG_SERVICE")
        activity, payment = data["activity"], data["payment"]
        metrics = {"delivered": integer(activity["delivered_transactions"]),
                   "settled_revenue_atoms": atoms(activity["settled_revenue_atomic"]),
                   "quotes_last_24h": integer(activity["quotes_last_24h"]),
                   "network": payment["network"], "asset_id": payment["asset"],
                   "cost_basis_fresh": data["operations"]["cost_basis_fresh"],
                   "excluded_from_external_revenue": payment["network"] != "eip155:8453",
                   "ledger_coverage": "public_aggregate_only", "cost": None, "profit": None}
        if type(metrics["cost_basis_fresh"]) is not bool:
            raise RevenueError("INVALID_COST_BASIS")
        if not metrics["cost_basis_fresh"]:
            findings.append(finding("COST_BASIS_EXPIRED", 10, "Refresh the approved facilitator fee evidence before offering paid purchases."))
        if metrics["quotes_last_24h"] == 0:
            findings.append(finding("NO_OBSERVED_QUOTES", 40,
                "Connect one real external agent use case to the existing matcher and free precheck; measure external quote requests separately from tests.",
                conversion_inference="Demand versus service blocking cannot be separated from these counters alone."))
        return metrics, findings
    if kind == "commerce_integrity":
        count = integer(data["confirmed_external_revenue"])
        receipts = integer(data["paid_receipts"])
        matched = integer(data["matched"])
        unmatched = integer(data["unmatched_receipts"])
        chain = integer(data["onchain_payments"])
        if count > matched or matched + unmatched != receipts or matched > chain:
            raise RevenueError("INCONSISTENT_AGGREGATE")
        amount = data["confirmed_amount"]
        if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,36})(?:\.[0-9]{1,36})?", amount):
            raise RevenueError("INVALID_AMOUNT")
        # This endpoint has no explicit asset or cost fields. Never assume USD == USDC.
        metrics = {"reported_external_payment_count": count, "reported_confirmed_amount": amount,
                   "asset_id": data.get("asset"), "network": data["network"],
                   "paid_receipts": receipts, "matched": matched, "unmatched_receipts": unmatched,
                   "historical_backfill": data["data_completeness"]["historical_chain_backfill"],
                   "counterparty_independently_verified": False,
                   "excluded_from_external_revenue": data.get("mode") != "mainnet" or data["network"] != "eip155:8453",
                   "ledger_coverage": "partial" if data["status"] != "ok" else "operator_claimed",
                   "cost": None, "profit": None}
        if metrics["excluded_from_external_revenue"]:
            findings.append(finding("PRODUCTION_SCOPE_UNVERIFIED", 15,
                "Exclude non-mainnet or unknown-scope counts from external revenue; retain them only as source claims."))
        if unmatched or metrics["historical_backfill"] != "complete":
            findings.append(finding("PAYMENT_RECONCILIATION_INCOMPLETE", 20,
                "Export minimal read-only receipt/settlement/delivery evidence; match network, asset, transfer log index and counterparty relation. Never recharge unmatched receipts.",
                unmatched_receipts=unmatched, historical_backfill=metrics["historical_backfill"]))
        findings.append(finding("COST_AND_NET_REVENUE_UNVERIFIED", 35,
            "Reconcile external receipts, delivery, refunds and actual costs in native asset units before reporting profit."))
        return metrics, findings
    if kind in ("pull_request", "bounty_pr"):
        if type(data["merged"]) is not bool or type(data["draft"]) is not bool or data["state"] not in ("open", "closed"):
            raise RevenueError("INVALID_PR")
        metrics = {"state": data["state"], "draft": data["draft"], "merged": data["merged"],
                   "head_sha": data["head"]["sha"], "html_url": data["html_url"],
                   "runtime_deployment_verified": False}
        if kind == "bounty_pr":
            metrics["payment_status"] = "UNKNOWN"
            if data["merged"]:
                findings.append(finding("BOUNTY_PAYMENT_UNVERIFIED", 25,
                    "Check the linked issue's resolved status, Drips points, ended Wave and reward-claim availability, then payment evidence. A merged PR alone is neither receivable recognition nor income; do not claim or withdraw funds automatically."))
        elif data["merged"]:
            findings.append(finding("DEPLOYMENT_UNVERIFIED", 30,
                "Verify the installed runtime commit and endpoint before declaring the feature active."))
        elif data["state"] == "open":
            findings.append(finding("IMPLEMENTED_NOT_RELEASED", 30,
                "Review the exact PR and CI, then verify installation after authorized release; do not assume a Draft PR is running."))
        else:
            findings.append(finding("CHANGE_CLOSED_UNMERGED", 30, "Review why the change closed and choose a current implementation."))
        return metrics, findings
    if kind == "github_issues":
        if type(data["complete"]) is not bool or not isinstance(data["pages"], list):
            raise RevenueError("INVALID_PAGINATION")
        issues = {}
        for page in data["pages"]:
            raw = page["raw_json"].encode("utf-8")
            if digest(raw) != page["sha256"]:
                raise RevenueError("PAGE_HASH_MISMATCH")
            for issue in strict_json(raw):
                if "pull_request" not in issue:
                    issues[integer(issue["id"])] = issue
        eligible = []
        excluded_scope = 0
        for issue in issues.values():
            labels = [label["name"].lower() for label in issue["labels"]]
            if not any("bounty" in label or "reward" in label or label == "stellar wave" for label in labels):
                continue
            if issue["state"] != "open" or issue.get("locked") is not False:
                continue
            if not isinstance(issue.get("assignees"), list):
                continue
            assignees = [item["login"].lower() for item in issue["assignees"]]
            if any(a != POLICY["candidate_owner"].lower() for a in assignees):
                continue
            if re.search(r"\bcontracts/", issue.get("body") or ""):
                excluded_scope += 1
                continue  # Initial engineering scope excludes smart-contract changes.
            eligible.append(issue)
        if not data["complete"]:
            findings.append(finding("SOURCE_PAGINATION_INCOMPLETE", 45,
                "Only the bounded pages were observed; do not assert that all available work was found."))
        for issue in eligible[:POLICY["max_candidates"]]:
            # Issue metadata never proves claimant eligibility, clear competition, or payout.
            findings.append(finding("BOUNTY_TERMS_UNVERIFIED", 50,
                "Read current issue, comments, linked PRs, assignment, Japan eligibility, AI policy, license, exact reward and payout terms before preparing an application.",
                subject=issue["id"], number=issue["number"], title=issue["title"], url=issue["html_url"],
                decision="B_RESEARCH_ONLY", competing_submission="UNKNOWN", payout="UNKNOWN"))
        return {"observed_issue_count": len(issues), "page_count": len(data["pages"]),
                "complete": data["complete"], "research_candidates": len(eligible),
                "excluded_contract_scope_count": excluded_scope,
                "execution_eligible_candidates": 0}, findings
    raise RevenueError("UNSUPPORTED_SOURCE")
