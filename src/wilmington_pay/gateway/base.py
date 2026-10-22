"""Gateway port. Any PCI-DSS Level 1 processor can be plugged in behind it
(Authorize.Net, PayPal/Braintree, Fiserv, Elavon, the City's incumbent...).
The City's choice is an open question (deck slide 25)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..card import CardData


class GatewayError(Exception):
    """Transport/processor failure where the charge outcome is UNKNOWN.
    Must not be retried blindly - it could double-charge the resident."""


@dataclass(frozen=True)
class ChargeRequest:
    amount_cents: int
    currency: str
    card: CardData
    idempotency_key: str
    description: str
    reference: str          # service request id, shows on the City's settlement report


@dataclass(frozen=True)
class ChargeResult:
    approved: bool
    transaction_id: str | None
    auth_code: str | None
    last4: str
    brand: str | None
    decline_code: str | None = None
    message: str | None = None


class PaymentGateway(Protocol):
    name: str

    def charge(self, request: ChargeRequest) -> ChargeResult: ...
