"""Append-only, native-asset reconciliation. There is no payment execution API."""
from dataclasses import dataclass
import re
from .contracts import fields, integer, require, text
from ..sources import digest, json_bytes, strict_json


@dataclass(frozen=True)
class VerifiedTransfer:
    chain_id: int
    tx_hash: str
    log_index: int
    asset: str
    atoms: int
    recipient: str
    sender: str
    relation: str
    synthetic: bool
    evidence: dict


def atoms(value):
    require(isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,77}", value), "INVALID_ATOMS", 400)
    return int(value)


def record_transfer(c, actor, request, proof):
    p = request["payload"]
    require(isinstance(proof, VerifiedTransfer), "HOST_VERIFICATION_REQUIRED")
    require(proof.chain_id == p["chain_id"] and proof.tx_hash == p["tx_hash"] and proof.log_index == p["log_index"], "PAYMENT_IDENTITY_MISMATCH")
    require(proof.relation in ("EXTERNAL_REVIEWED", "SELF", "INTERNAL", "UNKNOWN") and type(proof.synthetic) is bool, "INVALID_PAYMENT_PROOF")
    require(proof.synthetic == c.policy["synthetic"], "SYNTHETIC_BOUNDARY")
    require(type(proof.atoms) is int and 0 < proof.atoms < 2**256, "INVALID_TRANSFER_AMOUNT")
    c._observation(p["opportunity_id"])
    identity = f"eip155:{proof.chain_id}:{proof.tx_hash}:{proof.log_index}"
    event = "transfer:" + identity
    scope = actor.actor_id + ":payment"
    key = text(request["idempotency_key"], 200)
    raw_sha = digest(json_bytes(p))
    prior = c.db.one("SELECT sha FROM rc_idempotency WHERE scope=? AND key=?", (scope, key))
    require(not prior or prior["sha"] == raw_sha, "IDEMPOTENCY_CONFLICT")
    body = {"transfer_id": identity, "opportunity_id": p["opportunity_id"], "asset": proof.asset,
            "atoms": str(proof.atoms), "recipient": proof.recipient, "sender": proof.sender,
            "relation": proof.relation, "synthetic": proof.synthetic, "evidence": proof.evidence}
    existing = c.db.one("SELECT body FROM rc_ledger WHERE event_key=?", (event,))
    if existing:
        old = strict_json(existing["body"])
        # Confirmation counts can increase; immutable identity/economic fields may not.
        require({k: v for k, v in old.items() if k != "evidence"} == {k: v for k, v in body.items() if k != "evidence"}, "TRANSFER_CONFLICT")
        response = {"event_key": event, "replayed": True}
        c.db.sql.execute("INSERT OR IGNORE INTO rc_idempotency VALUES(?,?,?,?)", (scope, key, raw_sha, json_bytes(response)))
        return response
    # Caller idempotency is a separate constraint from transfer identity.
    c.db.sql.execute("INSERT INTO rc_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (event, p["opportunity_id"], "RECEIVED", proof.asset, str(proof.atoms), proof.relation, int(proof.synthetic), None, identity, json_bytes(body), c.clock()))
    response = {"event_key": event, "replayed": False, "earned": False}
    c.db.sql.execute("INSERT OR IGNORE INTO rc_idempotency VALUES(?,?,?,?)", (scope, key, raw_sha, json_bytes(response)))
    c._audit(actor, "payment-reconciled", identity, "RECORDED")
    return response


def record_delivery(c, actor, p, identity):
    fields(p, ("opportunity_id", "asset", "amount_atoms", "kind", "evidence_ref"))
    _, observation = c._observation(p["opportunity_id"])
    require(p["kind"] in ("SUBMITTED", "ACCEPTED"), "INVALID_DELIVERY_KIND", 400)
    atoms(p["amount_atoms"])
    text(p["asset"], 200)
    text(p["evidence_ref"], 1000)
    # One acceptance per engagement/asset; a re-polled merged PR cannot accumulate
    # new receivables. Split engagements must have distinct source external IDs.
    old = c.db.all("SELECT body FROM rc_delivery WHERE opportunity_id=? AND kind=?", (p["opportunity_id"], p["kind"]))
    require(not any(strict_json(r["body"])["asset"] == p["asset"] for r in old), "DELIVERY_ALREADY_RECORDED")
    c.db.sql.execute("INSERT INTO rc_delivery VALUES(?,?,?,?,?)", (identity, p["opportunity_id"], p["kind"], json_bytes({**p, "synthetic": observation["is_synthetic"]}), c.clock()))
    return {"delivery_id": identity, "kind": p["kind"], "settled_payment_inferred": False, "evidence_class": "authenticated_reviewer_attestation"}


def record_adjustment(c, actor, p, identity):
    fields(p, ("original_event", "kind", "amount_atoms", "evidence_ref"))
    require(p["kind"] in ("REFUND", "REORG", "DISPUTE"), "INVALID_ADJUSTMENT", 400)
    value = atoms(p["amount_atoms"])
    require(value > 0, "ZERO_ADJUSTMENT", 400)
    text(p["evidence_ref"], 1000)
    original = c.db.one("SELECT * FROM rc_ledger WHERE event_key=? AND kind='RECEIVED'", (p["original_event"],))
    require(original is not None, "ORIGINAL_TRANSFER_REQUIRED")
    prior = c.db.all("SELECT amount FROM rc_ledger WHERE related=?", (p["original_event"],))
    require(sum(int(r["amount"]) for r in prior) + value <= int(original["amount"]), "ADJUSTMENT_EXCEEDS_ORIGINAL")
    c.db.sql.execute("INSERT INTO rc_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (identity, original["opportunity_id"], p["kind"], original["asset"], str(value), original["relation"], original["synthetic"],
                      p["original_event"], None, json_bytes({**p, "evidence_class": "authenticated_reviewer_attestation"}), c.clock()))
    return {"event_key": identity, "history_preserved": True, "cash_move_executed": False}


def summary(db):
    grouped = {}
    for r in db.all("SELECT * FROM rc_ledger"):
        key = (r["opportunity_id"], r["asset"])
        group = grouped.setdefault(key, {"received": 0, "adjustments": 0, "excluded": 0, "accepted": 0})
        if r["synthetic"] or r["relation"] != "EXTERNAL_REVIEWED":
            if r["kind"] == "RECEIVED":
                group["excluded"] += int(r["amount"])
        elif r["kind"] == "RECEIVED":
            group["received"] += int(r["amount"])
        else:
            group["adjustments"] += int(r["amount"])
    for r in db.all("SELECT * FROM rc_delivery WHERE kind='ACCEPTED'"):
        p = strict_json(r["body"])
        if p["synthetic"]:
            continue
        group = grouped.setdefault((r["opportunity_id"], p["asset"]), {"received": 0, "adjustments": 0, "excluded": 0, "accepted": 0})
        group["accepted"] += int(p["amount_atoms"])
    balances = []
    for (opp, asset), g in sorted(grouped.items()):
        net = g["received"] - g["adjustments"]
        earned = min(max(net, 0), g["accepted"])
        balances.append({"opportunity_id": opp, "asset": asset, "received_atoms": str(g["received"]),
                         "adjustment_atoms": str(g["adjustments"]), "net_received_atoms": str(net),
                         "earned_and_received_atoms": str(earned), "deferred_atoms": str(max(0, net - g["accepted"])),
                         "unreceived_accepted_atoms": str(max(0, g["accepted"] - net)), "excluded_atoms": str(g["excluded"])})
    return {"balances": balances, "profit_microusd": None, "profit_status": "UNKNOWN_NO_AUDITED_FX_AND_FULL_COST_ALLOCATION",
            "payment_execution": False, "acceptance_evidence": "authenticated_reviewer_attestation"}
