from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch
from factory.revenue.control.adapters import EvmReceipts, TRANSFER_TOPIC, GitHubPublisher
from factory.revenue.control.contracts import ControlError
from factory.revenue.control.demo import ACTORS, SOURCE, engineering, envelope, opportunity
from factory.revenue.tests.test_controller import ControllerFixture
from factory.revenue.sources import json_bytes


class EvmVerification(unittest.TestCase):
    def setUp(self):
        self.tx, self.block, self.asset, self.payee, self.sender = "0x" + "d" * 64, "0x" + "e" * 64, "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
        self.request = {"opportunity_id": "fixture-opportunity", "chain_id": 8453, "tx_hash": self.tx, "log_index": 7}
        self.receipt = {"status": "0x1", "transactionHash": self.tx, "blockNumber": "0x10", "blockHash": self.block,
                        "logs": [{"logIndex": "0x7", "address": self.asset, "topics": [TRANSFER_TOPIC, "0x" + "0" * 24 + self.sender[2:], "0x" + "0" * 24 + self.payee[2:]], "data": "0x" + f"{1000:064x}"}]}
        self.values = {"eth_chainId": hex(8453), "eth_getTransactionReceipt": self.receipt, "eth_blockNumber": "0x20", "eth_getBlockByNumber": {"hash": self.block}}
        self.adapter = EvmReceipts({"8453": {"rpc_url": "https://example.invalid/rpc", "recipient": self.payee, "assets": [self.asset], "confirmations": 12, "synthetic": False}},
                                  {self.sender: {"relation": "EXTERNAL_REVIEWED", "review_ref": "fixture-relationship"}},
                                  {f"eip155:8453:{self.tx}:7": {"opportunity_id": "fixture-opportunity", "review_ref": "fixture-receipt-to-order"}})
        self.calls = []
        def rpc(url, method, params):
            self.calls.append(method)
            return deepcopy(self.values[method])
        self.adapter.rpc = rpc

    def test_receipt_proves_exact_asset_recipient_log_and_allocation(self):
        proof = self.adapter.verify(self.request)
        self.assertEqual(proof.atoms, 1000)
        self.assertEqual(proof.relation, "EXTERNAL_REVIEWED")
        self.assertEqual(proof.log_index, 7)
        self.assertEqual(proof.evidence["allocation_opportunity_id"], "fixture-opportunity")
        self.assertEqual(set(self.calls), {"eth_chainId", "eth_getTransactionReceipt", "eth_blockNumber", "eth_getBlockByNumber"})

    def test_chain_finality_reorg_recipient_status_and_log_fail_closed(self):
        originals = deepcopy(self.values)
        modifications = [
            lambda: self.values.update({"eth_chainId": "0x1"}),
            lambda: self.values.update({"eth_blockNumber": "0x11"}),
            lambda: self.values.update({"eth_getBlockByNumber": {"hash": "0x" + "f" * 64}}),
            lambda: self.values["eth_getTransactionReceipt"].update({"status": "0x0"}),
            lambda: self.values["eth_getTransactionReceipt"]["logs"][0].update({"removed": True}),
            lambda: self.values["eth_getTransactionReceipt"]["logs"][0].update({"address": self.sender}),
            lambda: self.values["eth_getTransactionReceipt"]["logs"][0]["topics"].__setitem__(2, "0x" + "0" * 24 + self.sender[2:]),
            lambda: self.values["eth_getTransactionReceipt"].update({"logs": []}),
        ]
        for index, change in enumerate(modifications):
            with self.subTest(case=index):
                self.values = deepcopy(originals)
                change()
                with self.assertRaises(ControlError):
                    self.adapter.verify(self.request)

    def test_unknown_counterparty_never_assumed_external(self):
        self.adapter.relationships.clear()
        self.assertEqual(self.adapter.verify(self.request).relation, "UNKNOWN")

    def test_unrelated_incoming_payment_cannot_be_attached_to_order(self):
        self.request["opportunity_id"] = "different-engagement"
        with self.assertRaisesRegex(ControlError, "PAYMENT_ALLOCATION_REVIEW_REQUIRED"):
            self.adapter.verify(self.request)


class PublisherVerification(unittest.TestCase):
    def setUp(self):
        self.target = "https://api.github.com/repos/example/fixture/issues/1/comments"
        self.publisher = GitHubPublisher([self.target], "synthetic-token", "fixture-owner")
        self.body = "Exact approved text\n<!-- kg-revenue:" + "a" * 32 + " -->"
        self.review = {"proposal_id": "b" * 32, "action": {"kind": "issue_comment", "action": {"target": self.target,
                       "body_utf8": self.body, "reconciliation_tag": "kg-revenue:" + "a" * 32}}}

    def test_changed_author_not_accepted_as_our_publication(self):
        with patch("factory.revenue.control.adapters.https", return_value=json_bytes({"id": 1, "body": self.body, "user": {"login": "someone-else"}})):
            with self.assertRaisesRegex(ControlError, "PUBLISH_RESPONSE_MISMATCH"):
                self.publisher.send(self.review, "effect", lambda: None)

    def test_reconciliation_exact_text_and_marker_and_actor(self):
        entry = {"id": 1, "body": self.body, "user": {"login": "fixture-owner"}, "html_url": "https://github.com/example/fixture/issues/1#issuecomment-1"}
        with patch("factory.revenue.control.adapters.https", return_value=json_bytes([entry])):
            self.assertTrue(self.publisher.reconcile(self.review, "effect")["confirmed"])
        with patch("factory.revenue.control.adapters.https", return_value=json_bytes([entry, {**entry, "id": 2}])):
            with self.assertRaisesRegex(ControlError, "EFFECT_STILL_UNKNOWN"):
                self.publisher.reconcile(self.review, "effect")


class MatchingAndRecovery(ControllerFixture):
    def test_kill_switch_blocks_pending_verifier_then_owner_can_cancel(self):
        job = self.completed()
        self.call("owner_approver", "stop", {"reason": "OWNER_STOP"})
        with self.assertRaisesRegex(ControlError, "RUNTIME_STOPPED"):
            self.c.verify(ACTORS["verifier"], envelope("verify-stopped", {"job_id": job["job_id"]}))
        self.call("owner_approver", "cancel-job", {"job_id": job["job_id"], "review_ref": "fixture-cancellation"})
        self.call("reconciler", "cost", {"reservation_id": job["job_id"], "actual": {"cash": 0, "work": 1, "human": 1}, "evidence_ref": "fixture-cancelled-cost"})
        self.assertTrue(self.resume()["enabled"])

    def test_unclaimed_expired_job_does_not_hold_slot_forever(self):
        self.admit()
        self.now += 121
        self.call("safety_monitor", "expire", {})
        self.assertEqual(self.c.db.one("SELECT state FROM rc_jobs")["state"], "EXPIRED")
        self.assertEqual(self.c.db.one("SELECT state FROM rc_reservations")["state"], "UNKNOWN")

    def test_matching_explicit_capabilities_and_unknown_cost(self):
        demand = {"languages": ["Python"], "max_cash_microusd": 0, "max_work_minutes": 2, "max_human_minutes": 1, "max_results": 5}
        result = self.call("agent_operator", "match", demand)
        self.assertEqual(len(result["matches"]), 1)
        self.assertFalse(result["commitment_authorized"])
        demand["languages"] = ["TypeScript"]
        self.assertEqual(self.call("agent_operator", "match", demand)["rejected"][0]["reason"], "EXECUTOR_CAPABILITY_MISMATCH")

    def test_restore_cancels_old_runner_and_keeps_cost_unknown(self):
        from pathlib import Path
        from factory.revenue.control.engine import Controller
        job = self.admit()
        self.call("runner", "claim", {"job_id": job["job_id"]})
        path = Path(self.temp.name) / "restored-runner.db"
        self.c.db.backup(path)
        restored = Controller(path, self.policy)
        try:
            self.assertEqual(restored.db.one("SELECT state FROM rc_jobs")["state"], "CANCELLED_ON_RESTORE")
            self.assertEqual(restored.db.one("SELECT state FROM rc_reservations")["state"], "UNKNOWN")
        finally:
            restored.close()
