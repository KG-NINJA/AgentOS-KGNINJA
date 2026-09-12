import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from factory.revenue.control.auth import Authenticator
from factory.revenue.control.demo import envelope, fixture_policy
from factory.revenue.control.engine import Controller
from factory.revenue.control.service import make_server


class HttpPermissions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "http.db"
        self.tokens = {"agent_operator": "a" * 40, "owner_approver": "o" * 40}
        tokens, path = self.tokens, self.path
        class Config:
            auth = Authenticator([{"actor_id": role, "role": role, "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "expires_at": int(time.time()) + 600} for role, token in tokens.items()])
            @staticmethod
            def controller(path):
                return Controller(path, fixture_policy())
        self.server = make_server(self.path, Config(), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, role=None, payload=None, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Authorization": "Bearer " + self.tokens[role]} if role else {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        conn.request("POST" if payload is not None else "GET", path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = conn.getresponse()
        value = (response.status, response.read(), dict(response.getheaders()))
        conn.close()
        return value

    def test_p05_real_http_owner_route_returns_403_to_agent(self):
        for op in ("approve", "budget", "resume", "revoke"):
            status, body, _ = self.request("/api/" + op, "agent_operator", envelope("agent-" + op, {}))
            self.assertEqual(status, 403, body)
        self.assertEqual(self.request("/api/summary")[0], 401)

    def test_owner_and_agent_consoles_csp_and_no_cookie_auth(self):
        status, html, headers = self.request("/owner")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.request("/api/summary", extra={"Cookie": "role=owner_approver"})[0], 401)

    def test_host_origin_and_internal_claim_routes_rejected(self):
        self.assertEqual(self.request("/api/summary", "owner_approver", extra={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request("/api/summary", "owner_approver", extra={"Origin": "https://evil.invalid"})[0], 403)
        for op in ("send", "verification-start"):
            self.assertEqual(self.request("/api/" + op, "owner_approver", envelope("hidden", {}))[0], 404)

    def test_owner_enable_http_and_replayed_body_conflict(self):
        _, body, _ = self.request("/api/policy", "owner_approver")
        policy = json.loads(body)
        request = envelope("resume-once", {"policy_sha256": policy["sha256"], "review_ref": "fixture-owner"})
        self.assertEqual(self.request("/api/resume", "owner_approver", request)[0], 200)
        request["payload"]["review_ref"] = "changed"
        self.assertEqual(self.request("/api/resume", "owner_approver", request)[0], 409)


if __name__ == "__main__":
    unittest.main()
