"""Credentials are host-provisioned. No browser cookie or model-supplied role."""
from pathlib import Path
import hashlib
import hmac
import time
from ..sources import strict_json
from .contracts import Principal, require, fields, text, integer, sha

ROLES = {"collector", "agent_operator", "owner_approver", "runner", "verifier", "publisher", "reconciler", "safety_monitor"}


class Authenticator:
    def __init__(self, credentials, clock=time.time):
        self.clock = clock
        require(isinstance(credentials, list) and 1 <= len(credentials) <= 32, "INVALID_CREDENTIALS", 400)
        seen = set()
        actors = set()
        for c in credentials:
            fields(c, ("actor_id", "role", "token_sha256", "expires_at"))
            text(c["actor_id"], 100)
            text(c["role"], 32)
            sha(c["token_sha256"])
            integer(c["expires_at"])
            require(c["role"] in ROLES and c["expires_at"] > 0, "INVALID_CREDENTIAL", 400)
            require(c["token_sha256"] not in seen and c["actor_id"] not in actors, "CREDENTIAL_ROLE_OVERLAP", 400)
            seen.add(c["token_sha256"])
            actors.add(c["actor_id"])
        self.credentials = credentials

    @classmethod
    def from_file(cls, path, **kwargs):
        p = Path(path).absolute()
        require(not any(x.is_symlink() for x in [p,*p.parents]) and not p.stat().st_mode & 0o077, "CREDENTIAL_FILE_UNSAFE", 400)
        return cls(strict_json(p.read_bytes()), **kwargs)

    def authenticate(self, authorization):
        require(isinstance(authorization, str) and authorization.startswith("Bearer "), "NEEDS_LOGIN", 401)
        token = authorization[7:]
        require(32 <= len(token) <= 512, "NEEDS_LOGIN", 401)
        value = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for credential in self.credentials:
            if hmac.compare_digest(value, credential["token_sha256"]):
                match = credential
        require(match is not None and match["expires_at"] > self.clock(), "NEEDS_LOGIN", 401)
        return Principal(match["actor_id"], match["role"])
