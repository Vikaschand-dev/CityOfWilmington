"""Domain model. Note: nothing in here ever holds a PAN, expiry or CVV."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    PENDING = "PENDING"            # created by agent, caller not yet in IVR
    IN_PROGRESS = "IN_PROGRESS"    # caller is in the payment IVR
    PROCESSING = "PROCESSING"      # gateway call in flight (lock state)
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"          # final: attempts exhausted
    FAILED = "FAILED"              # gateway error, outcome uncertain -> manual review
    CANCELLED = "CANCELLED"        # caller/agent abandoned
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = {
    SessionStatus.APPROVED,
    SessionStatus.DECLINED,
    SessionStatus.FAILED,
    SessionStatus.CANCELLED,
    SessionStatus.EXPIRED,
}
ACTIVE_STATUSES = {SessionStatus.PENDING, SessionStatus.IN_PROGRESS}


@dataclass
class LineItem:
    code: str
    label: str
    quantity: int
    unit_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_cents


@dataclass
class PaymentSession:
    session_id: str
    payment_code: str              # 6-digit one-time code, fallback when ANI match fails
    service_request_id: str
    amount_cents: int
    agent_id: str
    created_at: float
    expires_at: float
    status: SessionStatus = SessionStatus.PENDING
    currency: str = "USD"
    caller_ani: str | None = None
    line_items: list[dict] = field(default_factory=list)
    attempts: int = 0
    # Safe, non-sensitive result fields (PCI DSS allows last4 + brand)
    card_brand: str | None = None
    card_last4: str | None = None
    gateway_transaction_id: str | None = None
    auth_code: str | None = None
    decline_reason: str | None = None
    ivr_started_at: float | None = None
    completed_at: float | None = None
    updated_at: float | None = None
    ttl: int | None = None         # DynamoDB TTL epoch seconds

    def to_item(self) -> dict[str, Any]:
        item = asdict(self)
        item["status"] = self.status.value
        return {k: v for k, v in item.items() if v is not None}

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "PaymentSession":
        data = dict(item)
        data["status"] = SessionStatus(data["status"])
        # DynamoDB returns Decimal for numbers
        for k in ("amount_cents", "attempts", "ttl"):
            if k in data and data[k] is not None:
                data[k] = int(data[k])
        for k in ("created_at", "expires_at", "ivr_started_at", "completed_at", "updated_at"):
            if k in data and data[k] is not None:
                data[k] = float(data[k])
        data["line_items"] = [
            {**li, "quantity": int(li["quantity"]), "unit_cents": int(li["unit_cents"])}
            for li in data.get("line_items", [])
        ]
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})

    def public_view(self) -> dict[str, Any]:
        """What the agent console / API is allowed to see."""
        return {
            "sessionId": self.session_id,
            "paymentCode": self.payment_code,
            "serviceRequestId": self.service_request_id,
            "amount": f"{self.amount_cents / 100:.2f}",
            "amountCents": self.amount_cents,
            "currency": self.currency,
            "status": self.status.value,
            "attempts": self.attempts,
            "cardBrand": self.card_brand,
            "cardLast4": self.card_last4,
            "transactionId": self.gateway_transaction_id,
            "authCode": self.auth_code,
            "declineReason": self.decline_reason,
            "lineItems": self.line_items,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "completedAt": self.completed_at,
        }
