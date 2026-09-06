"""Synthetic fixtures and an executable local protocol walkthrough. No revenue."""
from copy import deepcopy
import json
from .contracts import Principal
from .engine import Controller, DEFAULT_POLICY, stamp
from .ledger import VerifiedTransfer
from ..sources import digest, json_bytes

SOURCE = "https://example.invalid/opportunities/1"
REPO = "https://example.invalid/repository"
TARGET = "https://api.github.com/repos/example/synthetic/issues/1/comments"
ACTORS = {r: Principal("synthetic-" + r, r) for r in ("collector", "owner_approver", "agent_operator", "runner", "verifier", "publisher", "reconciler", "safety_monitor")}


class FixtureSource:
    fingerprint = "synthetic-source"
    raw = b'{"fixture":"synthetic","state":"open","assignee":"KG"}'

    def read(self):
        return self.raw


class FixtureVerifier:
    fingerprint = "synthetic-verifier"

    def verify(self, job, artifact, profile):
        return {"passed": artifact["files"].get("src/value.py") == "value = 2\n", "checks": [{"fixture": "protocol_only"}],
                "evidence_sha256": digest(b"synthetic-protocol-check"), "isolation": "synthetic-no-code-execution"}


class FixturePublisher:
    fingerprint = "synthetic-publisher"

    def __init__(self):
        self.sends = 0
        self.lose_response = False

    def send(self, review, identity, check_running):
        check_running()
        self.sends += 1
        if self.lose_response:
            raise TimeoutError("synthetic lost response after effect")
        return {"confirmed": True, "external_id": "synthetic-1"}

    def reconcile(self, review, identity):
        return {"confirmed": self.sends == 1, "external_id": "synthetic-1"}


class FixturePayments:
    fingerprint = "synthetic-payments"

    def __init__(self, synthetic=True, relation="EXTERNAL_REVIEWED"):
        self.synthetic, self.relation = synthetic, relation

    def verify(self, p):
        return VerifiedTransfer(p["chain_id"], p["tx_hash"], p["log_index"], "eip155:8453/erc20:0x" + "a" * 40, 1000,
                                "0x" + "b" * 40, "0x" + "c" * 40, self.relation, self.synthetic, {"class": "synthetic_fixture"})


def fixture_policy(synthetic=True):
    policy = deepcopy(DEFAULT_POLICY)
    policy.update({"synthetic": synthetic, "sources": [SOURCE], "repositories": [REPO], "publication_targets": [TARGET], "allow_publication": True,
                   "cash_cap_microusd": 100, "adapter_fingerprints": {"source:" + SOURCE: FixtureSource.fingerprint, "verifier": FixtureVerifier.fingerprint,
                    "publisher": FixturePublisher.fingerprint, "payments": FixturePayments.fingerprint}})
    return policy


def opportunity(now, synthetic=True):
    return {"schema_version": "0.1.0", "record_kind": "opportunity_observation", "is_synthetic": synthetic,
            "source": {"provider": "github", "external_id": "fixture/1", "url": SOURCE, "observed_at": stamp(now),
                       "snapshot_sha256": digest(FixtureSource.raw), "snapshot_ref": "synthetic:source"},
            "reward": {"amount_min_atoms": "1000", "asset_id": "eip155:8453/erc20:0x" + "a" * 40, "decimals": 6,
                       "reward_type": "fixed", "terms_evidence_ref": "synthetic:terms", "usd_min_microusd": None, "fx_evidence_ref": None},
            "eligibility": {"japan_resident_allowed": "PASS", "ai_assistance_allowed": "PASS", "license_compatible": "PASS", "payout_route_usable": "PASS", "evidence_refs": ["synthetic:review"]},
            "availability": {"state": "OPEN", "assignee": "ASSIGNED_TO_KG", "claim_required": True, "claim_state": "CONFIRMED", "competing_submission": False, "deadline": None},
            "terms": {"requirements_sha256": digest(b"synthetic terms"), "acceptance_criteria": ["Value equals two"], "payment_due_at": None, "payment_terms_ref": "synthetic:payment-terms"},
            "work_scope": {"summary": "Synthetic local protocol demonstration", "languages": ["Python"], "allowed_paths": ["src"], "expected_tests": ["Value test"], "excluded_actions": ["payment"]},
            "estimates": {"compute_cost_upper_microusd": 0, "other_cash_cost_upper_microusd": 0, "human_minutes_upper": 1,
                          "runner_minutes_upper": 2, "p_assign_permyriad": None, "p_accept_given_assign_permyriad": None, "p_pay_given_accept_assign_permyriad": None,
                          "probability_basis": "UNKNOWN", "calibration_ref": None, "cost_basis_ref": "synthetic:included-allowance"},
            "risk_flags": [], "missing_evidence": [], "evidence_refs": ["synthetic:only"]}


def envelope(key, payload):
    return {"schema_version": "revenue-controller/0.2", "idempotency_key": key, "payload": payload}


def engineering(opp):
    return {"opportunity_id": opp, "kind": "engineering", "caps": {"cash": 0, "work": 2, "human": 1},
            "action": {"repo": REPO, "base_commit": "a" * 40, "objective": "Make the fixture value equal two", "allowed_paths": ["src"],
                       "denied_paths": ["tests"], "checks_profile": "fixture"}}


def run_demo(path, now):
    c = Controller(path, fixture_policy(), clock=lambda: now, sources={SOURCE: FixtureSource()}, verifier=FixtureVerifier(), publisher=FixturePublisher(), payments=FixturePayments())
    def call(role, op, payload):
        return c.call(ACTORS[role], op, envelope("demo-" + op, payload))
    try:
        call("owner_approver", "resume", {"policy_sha256": c.fingerprint, "review_ref": "synthetic:demo-only"})
        observed = call("collector", "observe", {"event_key": "synthetic:1", "opportunity": opportunity(now), "source_utf8": FixtureSource.raw.decode()})
        proposal = call("agent_operator", "propose", engineering(observed["opportunity_id"]))
        approval = call("owner_approver", "approve", {"bindings": proposal["bindings"], "expires_at": stamp(now + 600), "evidence_review_ref": "synthetic:review"})
        budget = call("owner_approver", "budget", {"caps": {"cash": 0, "work": 10, "human": 10}, "cost_basis_ref": "synthetic:budget", "starts_at": stamp(now), "ends_at": stamp(now + 3600)})
        admitted = call("agent_operator", "admit", {"proposal_id": proposal["proposal_id"], "approval_id": approval["approval_id"], "budget_id": budget["budget_id"]})
        claimed = call("runner", "claim", {"job_id": admitted["job"]["job_id"]})
        artifact = {"files": {"src/value.py": "value = 2\n"}}
        result = make_result(claimed["job"], artifact, now)
        call("runner", "result", {"fence": claimed["fence"], "result": result, "artifact": artifact})
        verified = c.verify(ACTORS["verifier"], envelope("demo-verify", {"job_id": admitted["job"]["job_id"]}))
        call("reconciler", "cost", {"reservation_id": admitted["job"]["job_id"], "actual": {"cash": 0, "work": 1, "human": 1}, "evidence_ref": "synthetic:actual"})
        payment = c.reconcile_payment(ACTORS["reconciler"], envelope("demo-payment", {"opportunity_id": observed["opportunity_id"], "chain_id": 8453, "tx_hash": "0x" + "d" * 64, "log_index": 0}))
        call("reconciler", "delivery", {"opportunity_id": observed["opportunity_id"], "asset": "eip155:8453/erc20:0x" + "a" * 40, "amount_atoms": "1000", "kind": "ACCEPTED", "evidence_ref": "synthetic:acceptance"})
        return {"synthetic": True, "actual_revenue": False, "verification": verified, "payment": payment, "summary": c.get(ACTORS["owner_approver"], "summary")}
    finally:
        c.close()


def make_result(job, artifact, now):
    return {"schema_version": "0.1.0", "is_synthetic": job["is_synthetic"], "job_id": job["job_id"], "status": "LOCAL_PASS", "base_commit": job["repo"]["commit"],
            "artifact_sha256": digest(json_bytes(artifact)), "artifact_ref": "sha256:" + digest(json_bytes(artifact)), "changed_paths": sorted(artifact["files"]),
            "tests": [{"name": "untrusted runner claim", "status": "PASS", "exit_code": 0, "log_ref": "synthetic:log"}], "quality_notes": [], "unresolved": [],
            "usage": {"cash_cost_microusd": 0, "cost_status": "ACTUAL", "cost_evidence_ref": "synthetic:claimed", "wall_seconds": 1, "human_minutes": 1},
            "submission_status": "NOT_SUBMITTED", "finished_at": stamp(now)}
