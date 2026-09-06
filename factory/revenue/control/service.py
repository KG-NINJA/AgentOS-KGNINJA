"""Loopback-only authenticated API. Owner and agents receive separate credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
from .adapters import EvmReceipts, GitHubPublisher, SnapshotSource
from .auth import Authenticator
from .contracts import ControlError, fields, require, text
from .engine import Controller, DEFAULT_POLICY
from .sandbox import ArtifactVerifier
from ..sources import RevenueError, json_bytes, strict_json


def private_json(path):
    p = Path(path).absolute()
    require(not any(x.is_symlink() for x in [p, *p.parents]) and p.is_file() and not p.stat().st_mode & 0o077
            and p.stat().st_size <= 2_000_000, "PRIVATE_CONFIG_PERMISSIONS", 400)
    return strict_json(p.read_bytes())


def token_from_env(name):
    require(isinstance(name, str) and re.fullmatch("KG_REVENUE_[A-Z0-9_]+", name), "INVALID_TOKEN_ENVIRONMENT_NAME")
    token = os.environ.get(name)
    require(token is not None, "NEEDS_LOGIN", 503)
    return text(token, 512)


class HostConfiguration:
    def __init__(self, path):
        config = private_json(path)
        fields(config, ("policy", "credentials_file", "source_adapters", "verifier_profiles", "publisher", "payments"))
        self.auth = Authenticator.from_file(config["credentials_file"])
        self.policy = config["policy"]
        fields(self.policy, tuple(DEFAULT_POLICY))
        require(self.policy["synthetic"] is False, "DEMO_IS_NOT_A_LIVE_SERVICE", 400)
        self.sources = {}
        for source, c in config["source_adapters"].items():
            fields(c, ("urls", "token_env"))
            self.sources[source] = SnapshotSource(c["urls"], token_from_env(c["token_env"]) if c["token_env"] else None)
        self.verifier = ArtifactVerifier(config["verifier_profiles"]) if config["verifier_profiles"] else None
        c = config["publisher"]
        self.publisher = None
        if c:
            fields(c, ("targets", "token_env", "login", "verified_heads"))
            self.publisher = GitHubPublisher(c["targets"], token_from_env(c["token_env"]), c["login"], c["verified_heads"])
        c = config["payments"]
        self.payments = None
        if c:
            fields(c, ("networks", "relationships", "allocations"))
            self.payments = EvmReceipts(c["networks"], c["relationships"], c["allocations"])
        # These hashes are computed from the protected host configuration, never
        # from a request. A code/config change invalidates outstanding approvals.
        bindings = {"source:" + k: v.fingerprint for k, v in self.sources.items()}
        bindings.update({k: v.fingerprint for k, v in (("verifier", self.verifier), ("publisher", self.publisher), ("payments", self.payments)) if v})
        self.policy = {**self.policy, "adapter_fingerprints": bindings}

    def controller(self, path):
        return Controller(path, self.policy, sources=self.sources, verifier=self.verifier, publisher=self.publisher, payments=self.payments)


def make_server(path, config, port=8789):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass  # Do not log bearer headers, request bodies or raw source text.

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json_bytes(data)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def handle_request(self, mutation):
            c = None
            try:
                authority = "127.0.0.1:" + str(self.server.server_port)
                require(self.headers.get("Host") == authority, "HOST_DENIED", 403)
                require(self.headers.get("Origin") in (None, "http://" + authority), "ORIGIN_DENIED", 403)
                require(not self.headers.get("Transfer-Encoding"), "TRANSFER_ENCODING_DENIED", 400)
                if not mutation and self.path in ("/", "/agent", "/owner", "/console.js", "/console.css"):
                    name = "console.html" if self.path in ("/", "/agent", "/owner") else self.path[1:]
                    data = Path(__file__).with_name("web").joinpath(name).read_bytes()
                    kind = "text/html" if name.endswith("html") else "text/javascript" if name.endswith("js") else "text/css"
                    self.respond(200, data, kind + "; charset=utf-8")
                    return
                actor = config.auth.authenticate(self.headers.get("Authorization"))
                c = config.controller(path)
                if not mutation:
                    if self.path == "/api/identity":
                        output = {"actor_id": actor.actor_id, "role": actor.role}
                    else:
                        require(self.path.startswith("/api/"), "NOT_FOUND", 404)
                        output = c.get(actor, self.path.removeprefix("/api/"))
                else:
                    require(self.headers.get("Content-Type") == "application/json" and len(self.headers.get_all("Content-Length", [])) == 1, "JSON_BODY_REQUIRED", 400)
                    length = self.headers["Content-Length"]
                    require(length.isdigit() and 0 < int(length) <= 2_000_000, "PAYLOAD_TOO_LARGE", 413)
                    self.connection.settimeout(10)
                    data = self.rfile.read(int(length))
                    require(len(data) == int(length), "BODY_TRUNCATED", 400)
                    request = strict_json(data)
                    require(self.path.startswith("/api/"), "NOT_FOUND", 404)
                    operation = self.path.removeprefix("/api/")
                    if operation == "execute":
                        output = c.execute(actor, request)
                    elif operation == "verify":
                        output = c.verify(actor, request)
                    elif operation == "reconcile-effect":
                        output = c.reconcile_effect(actor, request)
                    elif operation == "reconcile-payment":
                        output = c.reconcile_payment(actor, request)
                    else:
                        # Internal claim helpers cannot be invoked directly over HTTP.
                        require(operation not in ("send", "verification-start") and re.fullmatch("[a-z]+(?:-[a-z]+)*", operation), "NOT_FOUND", 404)
                        output = c.call(actor, operation, request)
                self.respond(200, output)
            except ControlError as exc:
                self.respond(exc.status, {"error": exc.code})
            except (RevenueError, ValueError, KeyError, TypeError, AttributeError):
                self.respond(400, {"error": "INVALID_REQUEST"})
            except Exception:
                self.respond(503, {"error": "HOST_CAPABILITY_UNAVAILABLE"})
            finally:
                if c:
                    c.close()

        def do_GET(self):
            self.handle_request(False)

        def do_POST(self):
            self.handle_request(True)

        def do_OPTIONS(self):
            self.respond(403, {"error": "CROSS_ORIGIN_ACCESS_DENIED"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
