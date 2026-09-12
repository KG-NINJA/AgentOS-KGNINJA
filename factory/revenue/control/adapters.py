"""Bounded host adapters. No signing key, model credential or generic URL proxy."""
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from pathlib import Path
from .contracts import ControlError, fields, integer, require, text
from .ledger import VerifiedTransfer
from ..sources import digest, json_bytes, strict_json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ControlError("REDIRECT_DENIED", 503)


def fingerprint(config):
    return digest(json_bytes({"config": config, "code_sha256": digest(Path(__file__).read_bytes())}))


def https(url, *, data=None, headers=None):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and not parsed.fragment, "UNSAFE_ADAPTER_URL", 400)
    request = Request(url, data=data, headers={"User-Agent": "KG-Revenue-Controller/0.2", **(headers or {})})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            require(response.status in (200, 201), "REMOTE_STATUS", 503)
            raw = response.read(2_000_001)
            require(len(raw) <= 2_000_000, "REMOTE_BODY_TOO_LARGE", 503)
            return raw
    except HTTPError as exc:
        raise ControlError("REMOTE_RATE_LIMITED" if exc.code in (403, 429) else "REMOTE_STATUS", 503) from exc


class SnapshotSource:
    """A host-selected URL returning the exact bytes bound by an observation.

    GitHub observations should use a composite of issue/comments/timeline and
    policy evidence, not a single issue body when those affect eligibility.
    """
    def __init__(self, urls, token=None):
        require(isinstance(urls, list) and 1 <= len(urls) <= 5 and all(isinstance(x, str) for x in urls), "INVALID_SOURCE_ADAPTER")
        self.urls, self.token = urls, token
        self.fingerprint = fingerprint({"type": "snapshot_source", "urls": urls, "authenticated": bool(token)})

    def read(self):
        values = []
        for url in self.urls:
            headers = {"Accept": "application/json"}
            if self.token and urlsplit(url).hostname == "api.github.com":
                headers["Authorization"] = "Bearer " + self.token
            # Composite canonicalization retains all returned content but does not
            # invent pagination completion. Host URLs must contain bounded pages.
            values.append({"url": url, "value": strict_json(https(url, headers=headers))})
        return json_bytes(values)


class GitHubPublisher:
    def __init__(self, targets, token, login, verified_heads=None):
        require(isinstance(targets, list) and all(re.fullmatch(r"https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(issues/[1-9][0-9]*/comments|pulls)", x) for x in targets), "INVALID_GITHUB_TARGET")
        text(token, 512)
        require(isinstance(login, str) and re.fullmatch("[A-Za-z0-9-]{1,39}", login), "INVALID_PUBLISHER_LOGIN")
        self.targets, self.token, self.login, self.verified_heads = targets, token, login, verified_heads or {}
        for head, binding in self.verified_heads.items():
            fields(binding, ("artifact_sha256", "base", "commit_url", "commit_sha"))
            require(re.fullmatch("https://api\\.github\\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commits/[A-Za-z0-9_.%/-]+", binding["commit_url"])
                    and re.fullmatch("[0-9a-f]{40,64}", binding["commit_sha"]), "INVALID_VERIFIED_HEAD")
        self.fingerprint = fingerprint({"type": "github", "targets": targets, "login": login, "verified_heads": self.verified_heads})

    def _headers(self):
        return {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}

    def _payload(self, review):
        p = review["action"]
        action = p["action"]
        require(action["target"] in self.targets, "DESTINATION_NOT_ALLOWLISTED")
        if p["kind"] == "issue_comment":
            require("/issues/" in action["target"], "WRONG_TARGET_KIND")
            return {"body": action["body_utf8"]}
        require(p["kind"] == "draft_pr" and action["target"].endswith("/pulls"), "WRONG_TARGET_KIND")
        # This adapter only opens a PR for an already uploaded, host-verified head.
        # It never creates Git blobs/refs as undeclared extra side effects.
        bound = self.verified_heads.get(action["head"])
        require(bound and bound["artifact_sha256"] == action["artifact_sha256"] and bound["base"] == action["base"], "HEAD_NOT_VERIFIED")
        return {"title": action["title"], "body": action["body_utf8"], "head": action["head"], "base": action["base"], "draft": True}

    def send(self, review, effect_id, check_running):
        payload = self._payload(review)
        action = review["action"]["action"]
        if review["action"]["kind"] == "draft_pr":
            bound = self.verified_heads[action["head"]]
            branch = strict_json(https(bound["commit_url"], headers=self._headers()))
            require(branch.get("sha") == bound["commit_sha"], "HEAD_CHANGED")
        check_running()
        raw = https(action["target"], data=json_bytes(payload), headers=self._headers())
        data = strict_json(raw)
        require(isinstance(data, dict) and type(data.get("id")) is int and data.get("body") == payload["body"] and data.get("user", {}).get("login") == self.login, "PUBLISH_RESPONSE_MISMATCH")
        return {"confirmed": True, "external_id": str(data["id"]), "url": data.get("html_url"), "response_sha256": digest(raw)}

    def reconcile(self, review, effect_id):
        payload = self._payload(review)
        target = review["action"]["action"]["target"]
        matches = []
        for page in range(1, 4):
            params = {"per_page": 100, "page": page}
            if review["action"]["kind"] == "draft_pr":
                params.update({"state": "all", "head": payload["head"], "base": payload["base"]})
            data = strict_json(https(target + "?" + urlencode(params), headers=self._headers()))
            require(isinstance(data, list), "RECONCILIATION_SHAPE")
            matches.extend(x for x in data if x.get("body") == payload["body"] and x.get("user", {}).get("login") == self.login and
                           (review["action"]["kind"] != "draft_pr" or x.get("head", {}).get("sha") == self.verified_heads[payload["head"]]["commit_sha"]))
            if len(data) < 100:
                break
        # Exact text alone is not unique; require the owner-approved payload to
        # contain its immutable proposal identity as a reconciliation marker.
        marker = review["action"]["action"]["reconciliation_tag"]
        require(marker in payload["body"] and len(matches) == 1, "EFFECT_STILL_UNKNOWN")
        return {"confirmed": True, "external_id": str(matches[0]["id"]), "url": matches[0].get("html_url"), "method": "exact_marker_body_and_head"}


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class EvmReceipts:
    """Receipt verification over a host-selected RPC; no transactions are sent.

    RPC testimony is identified as such, not a cryptographic audit of the chain.
    Counterparty relationship comes from a separately reviewed host registry.
    """
    def __init__(self, networks, relationships, allocations):
        self.networks, self.relationships, self.allocations = networks, relationships, allocations
        for chain, n in networks.items():
            fields(n, ("rpc_url", "recipient", "assets", "confirmations", "synthetic"))
            require(str(int(chain)) == chain and int(chain) > 0, "INVALID_CHAIN")
            integer(n["confirmations"], 100000)
            require(n["confirmations"] >= 1 and re.fullmatch("0x[0-9a-f]{40}", n["recipient"]), "INVALID_NETWORK_CONFIG")
            require(type(n["synthetic"]) is bool and isinstance(n["assets"], list) and n["assets"]
                    and all(re.fullmatch("0x[0-9a-f]{40}", x) for x in n["assets"]), "INVALID_ASSET_CONFIG")
        for entry in relationships.values():
            fields(entry, ("relation", "review_ref"))
            require(entry["relation"] in ("EXTERNAL_REVIEWED", "SELF", "INTERNAL"), "INVALID_RELATIONSHIP")
            text(entry["review_ref"], 500)
        for allocation in allocations.values():
            fields(allocation, ("opportunity_id", "review_ref"))
            text(allocation["opportunity_id"], 100)
            text(allocation["review_ref"], 500)
        self.fingerprint = fingerprint({"type": "evm_receipts", "networks": networks, "relationships": relationships, "allocations": allocations})

    def rpc(self, url, method, params):
        require(method in ("eth_chainId", "eth_getTransactionReceipt", "eth_blockNumber", "eth_getBlockByNumber"), "RPC_METHOD_DENIED")
        data = strict_json(https(url, data=json_bytes({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}), headers={"Content-Type": "application/json"}))
        require(isinstance(data, dict) and data.get("id") == 1 and "error" not in data and "result" in data, "RPC_ERROR", 503)
        return data["result"]

    def verify(self, p):
        integer(p["chain_id"])
        integer(p["log_index"])
        require(isinstance(p["tx_hash"], str) and re.fullmatch("0x[0-9a-f]{64}", p["tx_hash"]), "INVALID_TX_HASH", 400)
        n = self.networks.get(str(p["chain_id"]))
        require(n is not None, "CHAIN_NOT_ALLOWLISTED")
        url = n["rpc_url"]
        require(int(self.rpc(url, "eth_chainId", []), 16) == p["chain_id"], "RPC_CHAIN_MISMATCH")
        receipt = self.rpc(url, "eth_getTransactionReceipt", [p["tx_hash"]])
        require(isinstance(receipt, dict) and receipt.get("status") == "0x1" and receipt.get("transactionHash") == p["tx_hash"], "TRANSACTION_NOT_SETTLED")
        number = int(receipt["blockNumber"], 16)
        head = int(self.rpc(url, "eth_blockNumber", []), 16)
        block = self.rpc(url, "eth_getBlockByNumber", [receipt["blockNumber"], False])
        require(isinstance(block, dict) and block.get("hash") == receipt["blockHash"] and head - number + 1 >= n["confirmations"], "TRANSFER_NOT_FINAL")
        logs = [x for x in receipt["logs"] if int(x["logIndex"], 16) == p["log_index"]]
        require(len(logs) == 1, "TRANSFER_LOG_MISSING")
        event = logs[0]
        topics = event.get("topics", [])
        require(event.get("removed", False) is False and event["address"].lower() in n["assets"] and len(topics) == 3
                and topics[0].lower() == TRANSFER_TOPIC and all(re.fullmatch("0x0{24}[0-9a-fA-F]{40}", x) for x in topics[1:])
                and re.fullmatch("0x[0-9a-fA-F]{64}", event["data"]), "INVALID_TRANSFER_LOG")
        sender, recipient = "0x" + topics[1][-40:].lower(), "0x" + topics[2][-40:].lower()
        require(recipient == n["recipient"], "WRONG_RECIPIENT")
        relationship = self.relationships.get(sender, {"relation": "UNKNOWN", "review_ref": None})
        allocation = self.allocations.get(f"eip155:{p['chain_id']}:{p['tx_hash']}:{p['log_index']}")
        require(allocation and allocation["opportunity_id"] == p["opportunity_id"], "PAYMENT_ALLOCATION_REVIEW_REQUIRED")
        return VerifiedTransfer(p["chain_id"], p["tx_hash"], p["log_index"], f"eip155:{p['chain_id']}/erc20:{event['address'].lower()}", int(event["data"], 16), recipient, sender,
                                "SELF" if sender == recipient else relationship["relation"], n["synthetic"],
                                {"class": "host_rpc_receipt_and_counterparty_review", "receipt_sha256": digest(json_bytes(receipt)), "block_hash": receipt["blockHash"],
                                 "confirmations": head - number + 1, "relationship_review_ref": relationship["review_ref"],
                                 "allocation_opportunity_id": allocation["opportunity_id"], "allocation_review_ref": allocation["review_ref"]})
