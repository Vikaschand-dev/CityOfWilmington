"""Deterministic in-process gateway for demos and tests.

Test cards (any future expiry, any CVV):
  4111 1111 1111 1111  -> approved
  5555 5555 5555 4444  -> approved
  3782 822463 10005    -> approved (Amex, 4-digit CVV)
  4000 0000 0000 0002  -> declined (generic)
  4000 0000 0000 9995  -> declined (insufficient funds)
  4000 0000 0000 0119  -> GatewayError (processor timeout -> FAILED / manual review)
"""
from __future__ import annotations

import secrets
import threading

from .base import ChargeRequest, ChargeResult, GatewayError

_DECLINES = {
    "4000000000000002": ("card_declined", "The card was declined."),
    "4000000000009995": ("insufficient_funds", "The card has insufficient funds."),
}
_ERRORS = {"4000000000000119"}


class MockGateway:
    name = "mock"

    def __init__(self) -> None:
        self._seen: dict[str, ChargeResult] = {}
        self._lock = threading.Lock()

    def charge(self, request: ChargeRequest) -> ChargeResult:
        with self._lock:
            if request.idempotency_key in self._seen:   # emulate processor idempotency
                return self._seen[request.idempotency_key]
        card = request.card
        if card.pan in _ERRORS:
            raise GatewayError("Simulated processor timeout")
        if card.pan in _DECLINES:
            code, msg = _DECLINES[card.pan]
            result = ChargeResult(False, None, None, card.last4, card.brand, code, msg)
        else:
            result = ChargeResult(
                approved=True,
                transaction_id=f"MOCK-{secrets.token_hex(6).upper()}",
                auth_code=f"{secrets.randbelow(1_000_000):06d}",
                last4=card.last4,
                brand=card.brand,
                message="Approved",
            )
        with self._lock:
            self._seen[request.idempotency_key] = result
        return result
