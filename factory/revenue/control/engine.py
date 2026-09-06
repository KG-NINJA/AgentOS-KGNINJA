"""Host-owned workflow state. Proposals cannot grant themselves capabilities."""
from datetime import datetime, timezone
import math
import re
import time
from .contracts import ControlError, fields, integer, policy_hash, relative, require, role, schema, sha, text
from .database import Database
from ..sources import digest, instant, json_bytes, strict_json

DEFAULT_POLICY = {
    "schema_version": "revenue-controller/0.2", "synthetic": False,
    "sources": [], "repositories": [], "publication_targets": [],
    "cash_cap_microusd": 0, "work_cap_minutes": 120, "human_cap_minutes": 60,
    "approval_seconds": 900, "lease_seconds": 900, "fresh_seconds": 60,
    "allow_publication": False, "adapter_fingerprints": {},
}


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def decode(row, key):
    return strict_json(row[key])


def amount(value):
    fields(value, ("cash", "work", "human"))
    return {k: integer(value[k]) for k in ("cash", "work", "human")}


class Controller:
    def __init__(self, path, policy=None, *, clock=time.time, sources=None, verifier=None, publisher=None, payments=None):
        self.db = Database(path)
        self.clock = clock
        self.policy = strict_json(json_bytes(DEFAULT_POLICY if policy is None else policy))
        fields(self.policy, tuple(DEFAULT_POLICY))
        require(type(self.policy["synthetic"]) is bool and type(self.policy["allow_publication"]) is bool, "INVALID_POLICY", 400)
        for key in ("cash_cap_microusd", "work_cap_minutes", "human_cap_minutes", "approval_seconds", "lease_seconds", "fresh_seconds"):
            integer(self.policy[key])
        require(1 <= self.policy["fresh_seconds"] <= 60 and 1 <= self.policy["approval_seconds"] <= 900
                and 1 <= self.policy["lease_seconds"] <= 900, "INVALID_POLICY", 400)
        for key in ("sources", "repositories", "publication_targets"):
            require(type(self.policy[key]) is list and len(self.policy[key]) <= 30, "INVALID_POLICY", 400)
            for url in self.policy[key]:
                require(isinstance(url, str) and url.startswith("https://") and "@" not in url and "#" not in url, "UNSAFE_POLICY_URL", 400)
        self.sources, self.verifier, self.publisher, self.payments = sources or {}, verifier, publisher, payments
        # Injected adapters are a host interface, never a JSON/API field. Live adapters
        # must have their immutable configuration/code fingerprint bound to policy.
        for name, adapter in [("source:" + k, v) for k, v in self.sources.items()] + [("verifier", verifier), ("publisher", publisher), ("payments", payments)]:
            if adapter is not None and not self.policy["synthetic"]:
                require(getattr(adapter, "fingerprint", None) == self.policy["adapter_fingerprints"].get(name)
                        and getattr(adapter, "fingerprint", None) is not None, "ADAPTER_NOT_BOUND_TO_POLICY", 400)
        self.fingerprint = policy_hash(self.policy)
        self._fresh = {}

    def close(self):
        self.db.close()

    def get(self, actor, resource):
        role(actor, "collector", "agent_operator", "owner_approver", "runner", "verifier", "publisher", "reconciler", "safety_monitor")
        if resource == "policy":
            return {"policy": self.policy, "sha256": self.fingerprint}
        if resource == "summary":
            from .ledger import summary
            return {"runtime": self.db.one("SELECT * FROM rc_runtime"), "synthetic": self.policy["synthetic"],
                    "jobs": self.db.all("SELECT id,state,fence,lease_until FROM rc_jobs"),
                    "effects": self.db.all("SELECT id,state,attempts FROM rc_effects"),
                    "budgets": self.db.all("SELECT * FROM rc_budgets"), "ledger": summary(self.db),
                    "capabilities": {"source_refresh": sorted(self.sources), "verification": self.verifier is not None,
                                     "publication": self.publisher is not None and self.policy["allow_publication"],
                                     "payment_reconciliation": self.payments is not None, "payment_execution": False}}
        if resource == "opportunities":
            return [{"id": r["id"], "observation_sha256": r["sha"], "observation": decode(r, "body")}
                    for r in self.db.all("SELECT o.id,b.sha,b.body FROM rc_opportunities o JOIN rc_observations b ON b.id=o.latest_observation ORDER BY b.observed_at DESC LIMIT 100")]
        if resource == "proposals":
            return [self.review(r["id"]) for r in self.db.all("SELECT id FROM rc_proposals ORDER BY created_at DESC LIMIT 100")]
        raise ControlError("NOT_FOUND", 404)

    def _audit(self, actor, operation, subject, code):
        self.db.sql.execute("INSERT INTO rc_audit(actor,operation,subject,code,created_at) VALUES(?,?,?,?,?)",
                            (actor.actor_id, operation, subject, code, self.clock()))

    def _stop(self, code):
        self.db.sql.execute("UPDATE rc_runtime SET enabled=0,stop_reason=?,revision=revision+1 WHERE id=1", (code,))

    def _running(self):
        require(self.db.one("SELECT enabled FROM rc_runtime")["enabled"] == 1, "RUNTIME_STOPPED")
        require(not self.db.one("SELECT id FROM rc_reservations WHERE state='UNKNOWN' LIMIT 1"), "UNKNOWN_ACTUAL_COST")

    def call(self, actor, operation, request):
        self.last_replayed = False
        fields(request, ("schema_version", "idempotency_key", "payload"))
        require(request["schema_version"] == "revenue-controller/0.2", "SCHEMA_VERSION", 400)
        key = text(request["idempotency_key"], 200)
        require(isinstance(request["payload"], dict), "INVALID_PAYLOAD", 400)
        method = getattr(self, "op_" + operation.replace("-", "_"), None)
        require(method is not None and not operation.startswith("_"), "NOT_FOUND", 404)
        raw = json_bytes(request["payload"])
        require(len(raw) <= 2_000_000, "PAYLOAD_TOO_LARGE", 413)
        scope = actor.actor_id + ":" + actor.role + ":" + operation
        identity = digest((scope + ":" + key).encode())[:32]
        if operation in ("admit", "send"):
            role(actor, "agent_operator" if operation == "admit" else "publisher")
            prior = self.db.one("SELECT * FROM rc_idempotency WHERE scope=? AND key=?", (scope, key))
            if not prior:
                self._refresh(request["payload"].get("proposal_id"))
        with self.db.atomic():
            prior = self.db.one("SELECT * FROM rc_idempotency WHERE scope=? AND key=?", (scope, key))
            if prior:
                require(prior["sha"] == digest(raw), "IDEMPOTENCY_CONFLICT")
                self.last_replayed = True
                return decode(prior, "response")
            response = method(actor, request["payload"], identity)
            self.db.sql.execute("INSERT INTO rc_idempotency VALUES(?,?,?,?)", (scope, key, digest(raw), json_bytes(response)))
            self._audit(actor, operation, identity, "RECORDED")
            return response

    def op_observe(self, actor, p, identity):
        role(actor, "collector")
        fields(p, ("event_key", "opportunity", "source_utf8"))
        o = p["opportunity"]
        schema("opportunity", o)
        text(p["source_utf8"], 1_000_000)
        require(o["is_synthetic"] == self.policy["synthetic"], "SYNTHETIC_BOUNDARY")
        source = o["source"]
        require(source["url"] in self.policy["sources"], "SOURCE_NOT_ALLOWLISTED")
        require(source["snapshot_sha256"] == digest(p["source_utf8"].encode()), "SOURCE_HASH_MISMATCH")
        when = instant(source["observed_at"])
        require(when <= self.clock() + 5, "OBSERVATION_FROM_FUTURE")
        raw = json_bytes(o)
        event = text(p["event_key"], 200)
        previous = self.db.one("SELECT * FROM rc_observations WHERE event_key=?", (event,))
        if previous:
            require(previous["sha"] == digest(raw), "OBSERVATION_CONFLICT")
            return {"opportunity_id": previous["opportunity_id"], "observation_id": previous["id"], "replayed": True}
        opp = digest(json_bytes([source["provider"], source["external_id"], source["url"]]))[:32]
        self.db.sql.execute("INSERT OR IGNORE INTO rc_artifacts VALUES(?,?,?)", (source["snapshot_sha256"], p["source_utf8"].encode(), self.clock()))
        self.db.sql.execute("INSERT INTO rc_observations VALUES(?,?,?,?,?,?,?,?)",
                            (identity, event, opp, digest(raw), raw, when, actor.actor_id, int(o["is_synthetic"])))
        old = self.db.one("SELECT b.observed_at FROM rc_opportunities o JOIN rc_observations b ON b.id=o.latest_observation WHERE o.id=?", (opp,))
        if not old:
            self.db.sql.execute("INSERT INTO rc_opportunities VALUES(?,?,?)", (opp, source["url"], identity))
        elif when >= old["observed_at"]:
            self.db.sql.execute("UPDATE rc_opportunities SET latest_observation=? WHERE id=?", (identity, opp))
        return {"opportunity_id": opp, "observation_id": identity, "observation_sha256": digest(raw)}

    def _observation(self, opp):
        row = self.db.one("SELECT b.* FROM rc_opportunities o JOIN rc_observations b ON b.id=o.latest_observation WHERE o.id=?", (opp,))
        require(row is not None, "OPPORTUNITY_NOT_FOUND", 404)
        return row, decode(row, "body")

    def _refresh(self, proposal_id):
        review = self.review(proposal_id)
        row, o = self._observation(review["action"]["opportunity_id"])
        adapter = self.sources.get(o["source"]["url"])
        require(adapter is not None, "SOURCE_REFRESH_UNAVAILABLE", 503)
        try:
            raw = adapter.read()
        except Exception as exc:
            raise ControlError("SOURCE_REFRESH_FAILED", 503) from exc
        require(isinstance(raw, bytes) and digest(raw) == o["source"]["snapshot_sha256"], "SOURCE_CHANGED_REOBSERVE")
        self._fresh[proposal_id] = (row["sha"], self.clock())

    def _fresh_source(self, proposal_id, source_sha):
        proof = self._fresh.get(proposal_id)
        require(proof and proof[0] == source_sha and 0 <= self.clock() - proof[1] <= self.policy["fresh_seconds"], "FRESH_SOURCE_REQUIRED")

    def _eligible(self, o, kind):
        require(o["is_synthetic"] == self.policy["synthetic"], "SYNTHETIC_BOUNDARY")
        a = o["availability"]
        require(a["state"] == "OPEN" and a["assignee"] in ("UNASSIGNED", "ASSIGNED_TO_KG")
                and a["competing_submission"] is False, "SOURCE_UNAVAILABLE")
        require(not a["deadline"] or instant(a["deadline"]) > self.clock(), "DEADLINE_PASSED")
        require(all(v == "PASS" for k, v in o["eligibility"].items() if k != "evidence_refs")
                and o["eligibility"]["evidence_refs"], "ELIGIBILITY_UNKNOWN")
        require(o["terms"]["requirements_sha256"] and o["terms"]["payment_terms_ref"] and o["terms"]["acceptance_criteria"], "TERMS_UNKNOWN")
        require(not o["missing_evidence"] and not o["risk_flags"], "REVIEW_REQUIRED")
        if kind == "engineering" or kind == "draft_pr":
            require((a["claim_required"] is False and a["claim_state"] == "NOT_REQUIRED") or
                    (a["claim_required"] is True and a["claim_state"] == "CONFIRMED" and a["assignee"] == "ASSIGNED_TO_KG"), "ASSIGNMENT_REQUIRED")

    def op_propose(self, actor, p, identity):
        role(actor, "agent_operator")
        self._running()
        fields(p, ("opportunity_id", "kind", "action", "caps"))
        require(p["kind"] in ("engineering", "issue_comment", "draft_pr"), "ACTION_NOT_ALLOWED", 400)
        row, o = self._observation(text(p["opportunity_id"], 100))
        self._eligible(o, p["kind"])
        caps = amount(p["caps"])
        e = o["estimates"]
        require(e["cost_basis_ref"] and all(e[k] is not None for k in ("compute_cost_upper_microusd", "other_cash_cost_upper_microusd", "runner_minutes_upper", "human_minutes_upper")), "COST_UNKNOWN")
        require(caps["cash"] >= e["compute_cost_upper_microusd"] + e["other_cash_cost_upper_microusd"]
                and caps["work"] >= e["runner_minutes_upper"] and caps["human"] >= e["human_minutes_upper"], "CAP_BELOW_ESTIMATE")
        self._caps(caps)
        action = p["action"]
        if p["kind"] == "engineering":
            fields(action, ("repo", "base_commit", "objective", "allowed_paths", "denied_paths", "checks_profile"))
            require(action["repo"] in self.policy["repositories"], "REPOSITORY_NOT_ALLOWLISTED")
            require(isinstance(action["base_commit"], str) and re.fullmatch("[0-9a-f]{40,64}", action["base_commit"]), "INVALID_COMMIT", 400)
            text(action["objective"])
            text(action["checks_profile"], 200)
            require(type(action["allowed_paths"]) is list and 1 <= len(action["allowed_paths"]) <= 30, "NO_ALLOWED_PATHS", 400)
            require(type(action["denied_paths"]) is list and len(action["denied_paths"]) <= 30, "INVALID_DENIED_PATHS", 400)
            for path in action["allowed_paths"] + action["denied_paths"]:
                relative(path)
            require(set(action["allowed_paths"]) <= set(o["work_scope"]["allowed_paths"]), "PATH_OUTSIDE_OPPORTUNITY")
        else:
            fields(action, ("target", "body_utf8", "reconciliation_tag") if p["kind"] == "issue_comment" else ("target", "body_utf8", "reconciliation_tag", "title", "head", "base", "job_id", "artifact_sha256"))
            require(action["target"] in self.policy["publication_targets"], "DESTINATION_NOT_ALLOWLISTED")
            text(action["body_utf8"], 30000)
            require(isinstance(action["reconciliation_tag"], str) and re.fullmatch("kg-revenue:[0-9a-f]{32}", action["reconciliation_tag"])
                    and action["reconciliation_tag"] in action["body_utf8"], "RECONCILIATION_MARKER_REQUIRED")
            require(not any(decode(x, "payload")["action"].get("reconciliation_tag") == action["reconciliation_tag"]
                            for x in self.db.all("SELECT payload FROM rc_proposals WHERE kind!='engineering'")), "RECONCILIATION_MARKER_REUSED")
            if p["kind"] == "draft_pr":
                for key in ("title", "head", "base", "job_id"):
                    text(action[key], 200)
                sha(action["artifact_sha256"])
                verified = self.db.one("SELECT id FROM rc_verifications WHERE job_id=? AND artifact_sha=? AND passed=1", (action["job_id"], action["artifact_sha256"]))
                job = self.db.one("SELECT p.opportunity_id FROM rc_jobs j JOIN rc_proposals p ON p.id=j.proposal_id WHERE j.id=?", (action["job_id"],))
                require(verified and job and job["opportunity_id"] == p["opportunity_id"], "VERIFIED_ARTIFACT_REQUIRED")
        raw = json_bytes(p)
        self.db.sql.execute("INSERT INTO rc_proposals VALUES(?,?,?,?,?,?,?,?,?)",
                            (identity, p["opportunity_id"], p["kind"], raw, digest(raw), row["sha"], self.fingerprint, actor.actor_id, self.clock()))
        return self.review(identity)

    def _caps(self, caps):
        require(caps["cash"] <= self.policy["cash_cap_microusd"] and caps["work"] <= self.policy["work_cap_minutes"]
                and caps["human"] <= self.policy["human_cap_minutes"], "HOST_CAP_EXCEEDED")

    def review(self, proposal_id):
        row = self.db.one("SELECT * FROM rc_proposals WHERE id=?", (proposal_id,))
        require(row is not None, "PROPOSAL_NOT_FOUND", 404)
        payload = decode(row, "payload")
        return {"proposal_id": proposal_id, "action": payload, "bindings": {
            "proposal_id": proposal_id, "payload_sha256": row["payload_sha"], "source_sha256": row["source_sha"],
            "policy_sha256": row["policy_sha"], "destination": payload["action"].get("repo", payload["action"].get("target")), "caps": payload["caps"]}}

    def op_approve(self, actor, p, identity):
        role(actor, "owner_approver")
        fields(p, ("bindings", "expires_at", "evidence_review_ref"))
        text(p["evidence_review_ref"], 500)
        review = self.review(p["bindings"].get("proposal_id"))
        require(p["bindings"] == review["bindings"] and review["bindings"]["policy_sha256"] == self.fingerprint, "APPROVAL_BINDING_MISMATCH")
        row, _ = self._observation(review["action"]["opportunity_id"])
        require(row["sha"] == review["bindings"]["source_sha256"], "SOURCE_CHANGED")
        expiry = instant(p["expires_at"])
        require(self.clock() < expiry <= self.clock() + self.policy["approval_seconds"], "INVALID_APPROVAL_EXPIRY")
        self.db.sql.execute("INSERT INTO rc_approvals(id,proposal_id,owner,bindings,expires_at) VALUES(?,?,?,?,?)",
                            (identity, review["proposal_id"], actor.actor_id, json_bytes(p), expiry))
        return {"approval_id": identity, "expires_at": p["expires_at"]}

    def op_revoke(self, actor, p, identity):
        role(actor, "owner_approver")
        fields(p, ("approval_id",))
        require(self.db.sql.execute("UPDATE rc_approvals SET revoked=1 WHERE id=? AND consumed=0", (text(p["approval_id"], 100),)).rowcount == 1, "APPROVAL_NOT_REVOCABLE")
        return {"revoked": p["approval_id"]}

    def _approval(self, approval_id, proposal_id):
        a = self.db.one("SELECT * FROM rc_approvals WHERE id=? AND proposal_id=?", (approval_id, proposal_id))
        require(a and not a["revoked"] and not a["consumed"] and a["expires_at"] > self.clock(), "APPROVAL_UNAVAILABLE")
        review = self.review(proposal_id)
        require(decode(a, "bindings")["bindings"] == review["bindings"] and review["bindings"]["policy_sha256"] == self.fingerprint, "APPROVAL_BINDING_MISMATCH")
        row, o = self._observation(review["action"]["opportunity_id"])
        require(row["sha"] == review["bindings"]["source_sha256"], "SOURCE_CHANGED")
        self._eligible(o, review["action"]["kind"])
        return review, o

    def op_budget(self, actor, p, identity):
        role(actor, "owner_approver")
        fields(p, ("caps", "cost_basis_ref", "starts_at", "ends_at"))
        caps = amount(p["caps"])
        self._caps(caps)
        text(p["cost_basis_ref"], 500)
        start, end = instant(p["starts_at"]), instant(p["ends_at"])
        require(start <= self.clock() < end <= start + 86400, "INVALID_BUDGET_PERIOD")
        require(not self.db.one("SELECT id FROM rc_budgets WHERE starts<? AND ends>?", (end, start)), "OVERLAPPING_BUDGET")
        self.db.sql.execute("INSERT INTO rc_budgets(id,limit_cash,limit_work,limit_human,basis,owner,starts,ends) VALUES(?,?,?,?,?,?,?,?)",
                            (identity, caps["cash"], caps["work"], caps["human"], p["cost_basis_ref"], actor.actor_id, start, end))
        return {"budget_id": identity}

    def _reserve(self, identity, proposal_id, budget_id, caps):
        count = self.db.sql.execute("""UPDATE rc_budgets SET reserved_cash=reserved_cash+?,reserved_work=reserved_work+?,reserved_human=reserved_human+?
          WHERE id=? AND starts<=? AND ends>? AND spent_cash+reserved_cash+?<=limit_cash
          AND spent_work+reserved_work+?<=limit_work AND spent_human+reserved_human+?<=limit_human""",
          (caps["cash"], caps["work"], caps["human"], budget_id, self.clock(), self.clock(), caps["cash"], caps["work"], caps["human"])).rowcount
        require(count == 1, "BUDGET_NOT_AVAILABLE")
        self.db.sql.execute("INSERT INTO rc_reservations(id,proposal_id,budget_id,cash,work,human,state) VALUES(?,?,?,?,?,?,'RESERVED')",
                            (identity, proposal_id, budget_id, caps["cash"], caps["work"], caps["human"]))

    def op_admit(self, actor, p, identity):
        role(actor, "agent_operator")
        self._running()
        fields(p, ("proposal_id", "approval_id", "budget_id"))
        review, o = self._approval(p["approval_id"], p["proposal_id"])
        self._fresh_source(p["proposal_id"], review["bindings"]["source_sha256"])
        payload = review["action"]
        require(payload["kind"] == "engineering", "NOT_ENGINEERING")
        require(not self.db.one("SELECT id FROM rc_jobs WHERE state IN ('QUEUED','RUNNING','VERIFY_PENDING','VERIFYING')"), "EXECUTION_SLOT_FULL")
        self._reserve(identity, p["proposal_id"], p["budget_id"], payload["caps"])
        action, caps = payload["action"], payload["caps"]
        require(caps["work"] > 0, "WORK_BUDGET_REQUIRED")
        job = {"schema_version": "0.1.0", "is_synthetic": self.policy["synthetic"], "job_id": identity,
               "opportunity_key": payload["opportunity_id"], "source_snapshot_sha256": review["bindings"]["source_sha256"],
               "policy_sha256": self.fingerprint, "authorization_ref": p["approval_id"],
               "repo": {"url": action["repo"], "commit": action["base_commit"]}, "objective": action["objective"],
               "acceptance_criteria": o["terms"]["acceptance_criteria"], "allowed_paths": action["allowed_paths"], "denied_paths": action["denied_paths"],
               "allowed_side_effects": ["LOCAL_FILES_ONLY"], "resource_limits": {"max_wall_seconds": min(caps["work"] * 60, 7200), "max_attempts": 1,
               "incremental_cash_cap_microusd": caps["cash"], "max_changed_files": 30},
               "network_policy": "DENY_TEST_NETWORK_APPROVED_MODEL_PROXY_ONLY", "issued_at": stamp(self.clock()),
               "expires_at": stamp(self.clock() + min(caps["work"] * 60, 7200)), "artifact_namespace": identity,
               "required_result_schema": "execution-result.schema.json"}
        schema("job", job)
        self.db.sql.execute("INSERT INTO rc_jobs(id,proposal_id,approval_id,reservation_id,body,sha,state) VALUES(?,?,?,?,?,?,'QUEUED')",
                            (identity, p["proposal_id"], p["approval_id"], identity, json_bytes(job), digest(json_bytes(job))))
        self.db.sql.execute("UPDATE rc_approvals SET consumed=1 WHERE id=?", (p["approval_id"],))
        return {"job": job, "job_sha256": digest(json_bytes(job)), "checks_profile": action["checks_profile"]}

    def op_claim(self, actor, p, identity):
        role(actor, "runner")
        self._running()
        fields(p, ("job_id",))
        j = self.db.one("SELECT * FROM rc_jobs WHERE id=?", (p["job_id"],))
        require(j and j["state"] == "QUEUED", "JOB_NOT_CLAIMABLE")
        job = decode(j, "body")
        require(job["policy_sha256"] == self.fingerprint and instant(job["expires_at"]) > self.clock(), "JOB_EXPIRED_OR_POLICY_CHANGED")
        end = min(self.clock() + self.policy["lease_seconds"], instant(job["expires_at"]))
        self.db.sql.execute("UPDATE rc_jobs SET state='RUNNING',lease_actor=?,runner_actor=?,lease_until=?,fence=fence+1,attempts=attempts+1 WHERE id=?",
                            (actor.actor_id, actor.actor_id, end, j["id"]))
        return {"job": job, "fence": j["fence"] + 1, "lease_until": stamp(end)}

    def _lease(self, actor, job_id, fence):
        integer(fence)
        j = self.db.one("SELECT * FROM rc_jobs WHERE id=?", (job_id,))
        require(j and j["state"] == "RUNNING" and j["lease_actor"] == actor.actor_id and j["fence"] == fence
                and j["lease_until"] > self.clock(), "STALE_LEASE")
        return j

    def op_heartbeat(self, actor, p, identity):
        role(actor, "runner")
        self._running()
        fields(p, ("job_id", "fence"))
        j = self._lease(actor, p["job_id"], p["fence"])
        end = min(self.clock() + self.policy["lease_seconds"], instant(decode(j, "body")["expires_at"]))
        self.db.sql.execute("UPDATE rc_jobs SET lease_until=? WHERE id=?", (end, j["id"]))
        return {"lease_until": stamp(end), "fence": j["fence"]}

    def op_result(self, actor, p, identity):
        role(actor, "runner")
        fields(p, ("fence", "result", "artifact"))
        result = p["result"]
        schema("execution-result", result)
        j = self._lease(actor, result["job_id"], p["fence"])
        job = decode(j, "body")
        require(result["base_commit"] == job["repo"]["commit"] and result["is_synthetic"] == self.policy["synthetic"], "RESULT_BINDING_MISMATCH")
        artifact = p["artifact"]
        fields(artifact, ("files",))
        require(isinstance(artifact["files"], dict) and len(artifact["files"]) <= 30, "INVALID_ARTIFACT", 400)
        for path, content in artifact["files"].items():
            relative(path)
            require(any(path == x or path.startswith(x + "/") for x in job["allowed_paths"])
                    and not any(path == x or path.startswith(x + "/") for x in job["denied_paths"]), "ARTIFACT_PATH_DENIED")
            require(isinstance(content, str) and len(content.encode()) <= 500000 and "\x00" not in content, "ARTIFACT_CONTENT_DENIED")
        raw = json_bytes(artifact)
        require(result["artifact_sha256"] == digest(raw) and sorted(result["changed_paths"]) == sorted(artifact["files"]), "ARTIFACT_HASH_MISMATCH")
        self.db.sql.execute("INSERT OR IGNORE INTO rc_artifacts VALUES(?,?,?)", (digest(raw), raw, self.clock()))
        state = "VERIFY_PENDING" if result["status"] == "LOCAL_PASS" else "FAILED"
        self.db.sql.execute("UPDATE rc_jobs SET state=?,result_sha=?,result=? WHERE id=?", (state, digest(json_bytes(result)), json_bytes(result), j["id"]))
        # Runner estimates are never treated as audited actual costs.
        self.db.sql.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE id=?", (j["reservation_id"],))
        # Unknown cost blocks new commitments through _running(), while the
        # already budgeted verifier can finish. An explicit owner stop is retained.
        return {"job_id": j["id"], "state": state, "cost_state": "UNKNOWN"}

    def op_expire(self, actor, p, identity):
        role(actor, "safety_monitor", "owner_approver")
        fields(p, ())
        jobs = [j for j in self.db.all("SELECT * FROM rc_jobs WHERE state IN ('QUEUED','RUNNING','VERIFYING','VERIFY_PENDING')")
                if instant(decode(j, "body")["expires_at"]) <= self.clock() or
                (j["state"] == "RUNNING" and j["lease_until"] <= self.clock())]
        for job in jobs:
            self.db.sql.execute("UPDATE rc_jobs SET state='EXPIRED',fence=fence+1 WHERE id=?", (job["id"],))
            self.db.sql.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE id=?", (job["reservation_id"],))
        if jobs:
            self._stop("LEASE_LOST_RECONCILE_COST")
        return {"expired": [j["id"] for j in jobs], "automatic_retry": False}

    def op_cost(self, actor, p, identity):
        role(actor, "reconciler", "owner_approver")
        fields(p, ("reservation_id", "actual", "evidence_ref"))
        actual = amount(p["actual"])
        text(p["evidence_ref"], 500)
        r = self.db.one("SELECT * FROM rc_reservations WHERE id=?", (p["reservation_id"],))
        require(r and r["state"] != "FINAL", "COST_ALREADY_ALLOCATED")
        require(not self.db.one("SELECT id FROM rc_jobs WHERE reservation_id=? AND state IN ('RUNNING','QUEUED','VERIFY_PENDING','VERIFYING')", (r["id"],)), "WORK_STILL_ACTIVE")
        require(not self.db.one("SELECT id FROM rc_effects WHERE proposal_id=? AND state='SENDING'", (r["proposal_id"],)), "EFFECT_STILL_ACTIVE")
        self.db.sql.execute("""UPDATE rc_budgets SET reserved_cash=reserved_cash-?,reserved_work=reserved_work-?,reserved_human=reserved_human-?,
          spent_cash=spent_cash+?,spent_work=spent_work+?,spent_human=spent_human+? WHERE id=?""",
          (r["cash"], r["work"], r["human"], actual["cash"], actual["work"], actual["human"], r["budget_id"]))
        self.db.sql.execute("UPDATE rc_reservations SET state='FINAL',actual_cash=?,actual_work=?,actual_human=? WHERE id=?",
                            (actual["cash"], actual["work"], actual["human"], r["id"]))
        self.db.sql.execute("INSERT INTO rc_cost_events VALUES(?,?,?,?,?)", (identity, r["id"], json_bytes(p), actor.actor_id, self.clock()))
        if any(actual[k] > r[k] for k in actual):
            self._stop("ACTUAL_COST_OVERRUN")
        return {"reservation_id": r["id"], "actual": actual, "overrun": any(actual[k] > r[k] for k in actual)}

    def op_stop(self, actor, p, identity):
        role(actor, "owner_approver", "safety_monitor")
        fields(p, ("reason",))
        self._stop(text(p["reason"], 100))
        return {"stopped": True, "reconciliation_available": True}

    def op_resume(self, actor, p, identity):
        role(actor, "owner_approver")
        fields(p, ("policy_sha256", "review_ref"))
        require(p["policy_sha256"] == self.fingerprint, "POLICY_CHANGED")
        text(p["review_ref"], 500)
        require(not self.db.one("SELECT id FROM rc_effects WHERE state IN ('SENDING','UNKNOWN')"), "UNFINISHED_EFFECTS")
        require(not self.db.one("SELECT id FROM rc_reservations WHERE state='UNKNOWN'"), "UNKNOWN_ACTUAL_COST")
        require(not self.db.one("SELECT id FROM rc_budgets WHERE ends>? AND (spent_cash>limit_cash OR spent_work>limit_work OR spent_human>limit_human)", (self.clock(),)), "BUDGET_OVERRUN_UNRESOLVED")
        self.db.sql.execute("UPDATE rc_runtime SET enabled=1,stop_reason=NULL,revision=revision+1 WHERE id=1")
        return {"enabled": True, "publication_capability": self.policy["allow_publication"] and self.publisher is not None}

    def op_verification_start(self, actor, p, identity):
        role(actor, "verifier")
        fields(p, ("job_id",))
        require(self.db.one("SELECT enabled FROM rc_runtime")["enabled"] == 1, "RUNTIME_STOPPED")
        require(self.verifier is not None, "VERIFIER_UNAVAILABLE", 503)
        j = self.db.one("SELECT j.*,p.actor AS proposer FROM rc_jobs j JOIN rc_proposals p ON p.id=j.proposal_id WHERE j.id=?", (p["job_id"],))
        require(j and j["state"] == "VERIFY_PENDING", "NOT_READY_FOR_VERIFICATION")
        require(actor.actor_id not in (j["runner_actor"], j["proposer"]), "INDEPENDENT_VERIFIER_REQUIRED", 403)
        self.db.sql.execute("UPDATE rc_jobs SET state='VERIFYING' WHERE id=?", (j["id"],))
        return {"job_id": j["id"], "verification_id": identity, "state": "VERIFYING"}

    def verify(self, actor, request):
        started = self.call(actor, "verification-start", request)
        j = self.db.one("SELECT * FROM rc_jobs WHERE id=?", (started["job_id"],))
        existing = self.db.one("SELECT body FROM rc_verifications WHERE id=?", (started["verification_id"],))
        if existing:
            return decode(existing, "body")
        require(not self.last_replayed, "VERIFICATION_IN_FLIGHT_REVIEW_REQUIRED")
        # A verification interrupted after claiming cannot be restarted under the
        # same claim. A host review marks it failed; it never becomes an implicit PASS.
        require(j["state"] == "VERIFYING", "VERIFICATION_NOT_ACTIVE")
        result, job = decode(j, "result"), decode(j, "body")
        artifact = self.db.one("SELECT * FROM rc_artifacts WHERE sha=?", (result["artifact_sha256"],))
        proposal = self.review(j["proposal_id"])["action"]
        try:
            remaining = int(instant(job["expires_at"]) - self.clock())
            require(remaining > 0, "VERIFICATION_DEADLINE_EXPIRED")
            job["resource_limits"]["max_wall_seconds"] = min(job["resource_limits"]["max_wall_seconds"], remaining)
            report = self.verifier.verify(job, decode(artifact, "body"), proposal["action"]["checks_profile"])
            fields(report, ("passed", "checks", "evidence_sha256", "isolation"))
            require(type(report["passed"]) is bool and isinstance(report["checks"], list) and report["checks"], "INVALID_VERIFIER_REPORT")
            sha(report["evidence_sha256"])
        except Exception:
            report = {"passed": False, "checks": [{"error": "VERIFIER_UNAVAILABLE_OR_FAILED"}], "evidence_sha256": digest(b"unavailable"), "isolation": "unavailable"}
        report.update({"job_id": j["id"], "artifact_sha256": result["artifact_sha256"], "synthetic": self.policy["synthetic"]})
        with self.db.atomic():
            require(self.db.one("SELECT state FROM rc_jobs WHERE id=?", (j["id"],))["state"] == "VERIFYING", "VERIFICATION_STATE_CHANGED")
            self.db.sql.execute("INSERT INTO rc_verifications VALUES(?,?,?,?,?,?,?)", (started["verification_id"], j["id"], result["artifact_sha256"], actor.actor_id, json_bytes(report), int(report["passed"]), self.clock()))
            self.db.sql.execute("UPDATE rc_jobs SET state=? WHERE id=?", ("VERIFIED" if report["passed"] else "FAILED", j["id"]))
            self._audit(actor, "verification-complete", j["id"], "PASS" if report["passed"] else "FAIL")
        return report

    def op_send(self, actor, p, identity):
        role(actor, "publisher")
        self._running()
        fields(p, ("proposal_id", "approval_id", "budget_id"))
        require(self.policy["allow_publication"] and self.publisher is not None, "PUBLICATION_DISABLED", 503)
        review, _ = self._approval(p["approval_id"], p["proposal_id"])
        self._fresh_source(p["proposal_id"], review["bindings"]["source_sha256"])
        require(review["action"]["kind"] in ("issue_comment", "draft_pr"), "EXTERNAL_ACTION_NOT_ALLOWED")
        self._reserve(identity, p["proposal_id"], p["budget_id"], review["action"]["caps"])
        self.db.sql.execute("INSERT INTO rc_effects(id,proposal_id,approval_id,state,attempts) VALUES(?,?,?,'SENDING',1)",
                            (identity, p["proposal_id"], p["approval_id"]))
        self.db.sql.execute("INSERT INTO rc_effect_events(effect_id,state,evidence,created_at) VALUES(?,'SENDING',?,?)", (identity, json_bytes(review["bindings"]), self.clock()))
        self.db.sql.execute("UPDATE rc_approvals SET consumed=1 WHERE id=?", (p["approval_id"],))
        return {"effect_id": identity, "state": "SENDING"}

    def execute(self, actor, request):
        # Persist intent BEFORE crossing the network. A replay only reconciles;
        # a process crash between persistence and send also requires review.
        started = self.call(actor, "send", request)
        if self.last_replayed:
            return self.db.one("SELECT id AS effect_id,state FROM rc_effects WHERE id=?", (started["effect_id"],))
        effect_id = started["effect_id"]
        proposal = self.review(request["payload"]["proposal_id"])
        try:
            # Adapter checks the host kill-switch immediately before sending.
            report = self.publisher.send(proposal, effect_id, self._running)
            require(isinstance(report, dict) and report.get("confirmed") is True and isinstance(report.get("external_id"), str), "UNCONFIRMED_EFFECT")
            state = "CONFIRMED"
        except Exception:
            report, state = {"reason": "SEND_OUTCOME_UNCERTAIN", "resend_allowed": False}, "UNKNOWN"
        with self.db.atomic():
            self._effect_record(effect_id, state, report)
            self.db.sql.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE id=?", (effect_id,))
            self._stop("EFFECT_RECONCILIATION_REQUIRED" if state == "UNKNOWN" else "COST_RECONCILIATION_REQUIRED")
        return {"effect_id": effect_id, "state": state, "evidence": report}

    def _effect_record(self, identity, state, evidence):
        self.db.sql.execute("UPDATE rc_effects SET state=?,result=? WHERE id=?", (state, json_bytes(evidence), identity))
        self.db.sql.execute("INSERT INTO rc_effect_events(effect_id,state,evidence,created_at) VALUES(?,?,?,?)", (identity, state, json_bytes(evidence), self.clock()))

    def reconcile_effect(self, actor, request):
        role(actor, "reconciler")
        fields(request, ("schema_version", "idempotency_key", "payload"))
        fields(request["payload"], ("effect_id",))
        require(request["schema_version"] == "revenue-controller/0.2", "SCHEMA_VERSION", 400)
        key = text(request["idempotency_key"], 200)
        scope = actor.actor_id + ":effect-reconcile"
        raw_sha = digest(json_bytes(request["payload"]))
        prior = self.db.one("SELECT * FROM rc_idempotency WHERE scope=? AND key=?", (scope, key))
        if prior:
            require(prior["sha"] == raw_sha, "IDEMPOTENCY_CONFLICT")
            return decode(prior, "response")
        effect = self.db.one("SELECT * FROM rc_effects WHERE id=?", (request["payload"]["effect_id"],))
        require(effect is not None, "EFFECT_NOT_FOUND", 404)
        require(self.publisher is not None, "RECONCILER_UNAVAILABLE", 503)
        try:
            report = self.publisher.reconcile(self.review(effect["proposal_id"]), effect["id"])
        except Exception as exc:
            raise ControlError("RECONCILIATION_UNAVAILABLE", 503) from exc
        require(isinstance(report, dict) and report.get("confirmed") is True and isinstance(report.get("external_id"), str), "EFFECT_STILL_UNKNOWN")
        with self.db.atomic():
            prior = self.db.one("SELECT * FROM rc_idempotency WHERE scope=? AND key=?", (scope, key))
            if prior:
                require(prior["sha"] == raw_sha, "IDEMPOTENCY_CONFLICT")
                return decode(prior, "response")
            self._effect_record(effect["id"], "CONFIRMED", report)
            self._audit(actor, "effect-reconcile", effect["id"], "CONFIRMED")
            response = {"effect_id": effect["id"], "state": "CONFIRMED", "evidence": report}
            self.db.sql.execute("INSERT INTO rc_idempotency VALUES(?,?,?,?)", (scope, key, raw_sha, json_bytes(response)))
        return response

    def reconcile_payment(self, actor, request):
        from .ledger import record_transfer
        role(actor, "reconciler")
        fields(request, ("schema_version", "idempotency_key", "payload"))
        require(request["schema_version"] == "revenue-controller/0.2", "SCHEMA_VERSION", 400)
        fields(request["payload"], ("opportunity_id", "chain_id", "tx_hash", "log_index"))
        require(self.payments is not None, "PAYMENT_RECONCILER_UNAVAILABLE", 503)
        # Re-reading an existing payment identity is safe even after a lost DB write;
        # this adapter has no signing/charging method.
        proof = self.payments.verify(request["payload"])
        with self.db.atomic():
            return record_transfer(self, actor, request, proof)

    def op_delivery(self, actor, p, identity):
        from .ledger import record_delivery
        role(actor, "owner_approver", "reconciler")
        return record_delivery(self, actor, p, identity)

    def op_adjustment(self, actor, p, identity):
        from .ledger import record_adjustment
        role(actor, "owner_approver", "reconciler")
        return record_adjustment(self, actor, p, identity)

    def op_capability_failure(self, actor, p, identity):
        role(actor, "runner", "safety_monitor", "collector", "agent_operator")
        fields(p, ("capability", "code"))
        text(p["capability"], 100)
        require(p["code"] in ("NEEDS_LOGIN", "TOOL_UNAVAILABLE", "ISOLATION_UNAVAILABLE", "RATE_LIMITED"), "INVALID_FAILURE", 400)
        self._audit(actor, "capability-failure", p["capability"], p["code"])
        self._stop(p["code"])
        return {"state": "BLOCKED", "code": p["code"]}

    def op_match(self, actor, p, identity):
        from .matching import match
        return match(self, actor, p)

    def op_cancel_job(self, actor, p, identity):
        role(actor, "owner_approver")
        fields(p, ("job_id", "review_ref"))
        text(p["review_ref"], 500)
        j = self.db.one("SELECT * FROM rc_jobs WHERE id=?", (p["job_id"],))
        require(j and j["state"] in ("QUEUED", "RUNNING", "VERIFY_PENDING", "VERIFYING"), "JOB_NOT_CANCELLABLE")
        self.db.sql.execute("UPDATE rc_jobs SET state='CANCELLED',fence=fence+1,lease_until=NULL WHERE id=?", (j["id"],))
        self.db.sql.execute("UPDATE rc_reservations SET state='UNKNOWN' WHERE id=?", (j["reservation_id"],))
        self._stop("OWNER_JOB_CANCELLED_RECONCILE_COST")
        return {"job_id": j["id"], "state": "CANCELLED", "host_runner_must_terminate": True}
