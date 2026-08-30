"""HTTP routing adapter for the existing AgentOS2 control-plane server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .commerce import (
    CommerceService,
    PaymentConfig,
    ReceiptSigner,
    discovery_documents,
)
from .store import ArtifactStore


@dataclass(frozen=True)
class HttpRouteResponse:
    status: int
    body: dict | str
    headers: dict[str, str]
    content_type: str = "application/json; charset=utf-8"


class SwarmHttpRouter:
    def __init__(self, service: CommerceService):
        self.service = service

    def handle_get(
        self,
        raw_path: str,
        headers: Mapping[str, str],
    ) -> HttpRouteResponse | None:
        parsed = urlsplit(raw_path)
        documents = discovery_documents(self.service.base_url, self.service.payment_ready)
        if parsed.path in documents:
            body = documents[parsed.path]
            content_type = (
                "text/plain; charset=utf-8" if isinstance(body, str) else "application/json; charset=utf-8"
            )
            return HttpRouteResponse(200, body, {}, content_type)
        if parsed.path == "/swarm/status":
            return HttpRouteResponse(
                200,
                {
                    **self.service.store.summary(),
                    "payment_ready": self.service.payment_ready,
                    "real_execution_enabled": False,
                },
                {},
            )
        route = self._match_product(parsed.path)
        if route is None:
            return None
        product, subject = route
        query = parse_qs(parsed.query)
        level = query.get("level", ["free"])[0]
        commerce = self.service.request(
            product=product,
            subject=subject,
            level=level,
            payment_signature=headers.get("PAYMENT-SIGNATURE"),
            consumer_ref=headers.get("X-Agent-Id", "anonymous"),
        )
        return HttpRouteResponse(commerce.status, commerce.body, commerce.headers)

    @staticmethod
    def _match_product(path: str) -> tuple[str, str] | None:
        for product in ("signal", "research", "risk", "event", "consensus", "counter-thesis"):
            prefix = f"/{product}/"
            if path.startswith(prefix):
                subject = unquote(path[len(prefix) :]).strip()
                if subject and "/" not in subject:
                    return product, subject
        return None


def default_router(root: str | Path) -> SwarmHttpRouter:
    root_path = Path(root)
    store = ArtifactStore(root_path / "runtime" / "swarm" / "swarm.db")
    base_url = os.environ.get("SWARM_BASE_URL", "http://127.0.0.1:8787")
    payment_fields = {
        "network": os.environ.get("SWARM_X402_NETWORK"),
        "asset": os.environ.get("SWARM_X402_ASSET"),
        "pay_to": os.environ.get("SWARM_X402_PAY_TO"),
    }
    payment_config = None
    if all(payment_fields.values()):
        payment_config = PaymentConfig(**payment_fields)
    signer = None
    receipt_key = os.environ.get("SWARM_RECEIPT_HMAC_KEY")
    if receipt_key:
        signer = ReceiptSigner(receipt_key.encode("utf-8"))
    # The default verifier rejects and therefore never advertises payment-ready,
    # even when address metadata is present.
    service = CommerceService(
        store,
        base_url=base_url,
        payment_config=payment_config,
        receipt_signer=signer,
    )
    return SwarmHttpRouter(service)
