"""Application service - the single place where payment business rules live.
Used identically by the Lambda handlers and the local simulator."""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .card import CardData
from .config import Settings
from .gateway import ChargeRequest, GatewayError, PaymentGateway
from .models import ACTIVE_STATUSES, LineItem, PaymentSession, SessionStatus
from .redaction import get_logger
from .repository import SessionRepository

log = get_logger(__name__)


class PaymentError(Exception):
    """Business-rule violation with a message safe to show agents."""


@dataclass(frozen=True)
class PaymentOutcome:
    status: SessionStatus
    approved: bool
    retry_allowed: bool
    message: str
    session: PaymentSession


class PaymentService:
    def __init__(
        self,
        repo: SessionRepository,
        gateway: PaymentGateway,
        settings: Settings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = repo
        self.gateway = gateway
        self.settings = settings
        self.clock = clock

    # ------------------------------------------------------------- agent side
    def price(self, items: list[dict]) -> list[LineItem]:
        if not items:
            raise PaymentError("At least one pickup item is required.")
        lines = []
        for it in items:
            code, qty = str(it.get("code", "")), int(it.get("quantity", 0))
            fee = self.settings.fee_schedule.get(code)
            if fee is None:
                raise PaymentError(f"Unknown pickup item {code!r}.")
            if not 1 <= qty <= 20:
                raise PaymentError(f"Quantity for {code} must be 1-20.")
            lines.append(LineItem(code, fee["label"], qty, fee["unit_cents"]))
        return lines

    def create_session(
        self,
        service_request_id: str,
        items: list[dict],
        agent_id: str,
        caller_ani: str | None = None,
    ) -> PaymentSession:
        if not service_request_id:
            raise PaymentError("A service request id is required.")
        lines = self.price(items)
        amount = sum(li.total_cents for li in lines)
        if amount > self.settings.max_amount_cents:
            raise PaymentError("Amount exceeds the configured maximum for phone payments.")

        now = self.clock()
        session = PaymentSession(
            session_id=str(uuid.uuid4()),
            payment_code=self._unique_code(now),
            service_request_id=service_request_id,
            amount_cents=amount,
            agent_id=agent_id,
            caller_ani=_normalise_ani(caller_ani),
            created_at=now,
            expires_at=now + self.settings.session_ttl_seconds,
            updated_at=now,
            line_items=[{**li.__dict__, "total_cents": li.total_cents} for li in lines],
            ttl=int(now + self.settings.record_retention_days * 86400),
        )
        self.repo.put(session)
        log.info("payment session created", extra={"ctx": {
            "event": "session_created", "sessionId": session.session_id,
            "serviceRequestId": service_request_id, "amountCents": amount, "agentId": agent_id}})
        return session

    def get_session(self, session_id: str) -> PaymentSession | None:
        session = self.repo.get(session_id)
        if session and session.status in ACTIVE_STATUSES and session.expires_at <= self.clock():
            session = self.repo.transition(
                session_id, ACTIVE_STATUSES, {"status": SessionStatus.EXPIRED, "updated_at": self.clock()}
            ) or self.repo.get(session_id)
        return session

    def cancel_session(self, session_id: str, reason: str = "cancelled") -> PaymentSession | None:
        return self.repo.transition(
            session_id, ACTIVE_STATUSES,
            {"status": SessionStatus.CANCELLED, "decline_reason": reason,
             "updated_at": self.clock(), "completed_at": self.clock()},
        )

    # -------------------------------------------------------------- IVR side
    def resolve_for_caller(self, ani: str | None = None, code: str | None = None) -> PaymentSession | None:
        now = self.clock()
        if code:
            return self.repo.find_active_by_code(code.strip(), now)
        if ani:
            return self.repo.find_active_by_ani(_normalise_ani(ani) or "", now)
        return None

    def mark_ivr_started(self, session_id: str) -> PaymentSession | None:
        now = self.clock()
        return self.repo.transition(
            session_id, [SessionStatus.PENDING],
            {"status": SessionStatus.IN_PROGRESS, "ivr_started_at": now, "updated_at": now},
        ) or self.repo.get(session_id)

    def process_payment(self, session_id: str, card: CardData) -> PaymentOutcome:
        now = self.clock()
        current = self.repo.get(session_id)
        if current is None:
            raise PaymentError("Payment session not found.")
        if current.expires_at <= now:
            self.get_session(session_id)  # flips to EXPIRED
            raise PaymentError("This payment session has expired.")

        # Compare-and-set lock: only one in-flight charge per session, ever.
        locked = self.repo.transition(
            session_id, ACTIVE_STATUSES,
            {"status": SessionStatus.PROCESSING, "attempts": current.attempts + 1, "updated_at": now},
        )
        if locked is None:
            raise PaymentError("This payment is already being processed or is complete.")

        attempt = locked.attempts
        request = ChargeRequest(
            amount_cents=locked.amount_cents,
            currency=locked.currency,
            card=card,
            idempotency_key=f"{locked.session_id[:14]}-{attempt}",
            description=f"City of Wilmington 311 special pickup {locked.service_request_id}",
            reference=locked.service_request_id,
        )
        try:
            result = self.gateway.charge(request)
        except GatewayError as exc:
            # Outcome unknown -> never auto-retry; route to manual reconciliation.
            session = self.repo.transition(session_id, [SessionStatus.PROCESSING], {
                "status": SessionStatus.FAILED, "decline_reason": f"gateway_error: {exc}",
                "card_brand": card.brand, "card_last4": card.last4,
                "updated_at": self.clock(), "completed_at": self.clock()})
            log.error("gateway error", extra={"ctx": {"event": "payment_failed", "sessionId": session_id,
                                                      "attempt": attempt, "error": str(exc)}})
            return PaymentOutcome(SessionStatus.FAILED, False, False,
                                  "We could not reach the payment processor.", session)

        if result.approved:
            session = self.repo.transition(session_id, [SessionStatus.PROCESSING], {
                "status": SessionStatus.APPROVED, "gateway_transaction_id": result.transaction_id,
                "auth_code": result.auth_code, "card_brand": result.brand, "card_last4": result.last4,
                "decline_reason": None, "updated_at": self.clock(), "completed_at": self.clock()})
            log.info("payment approved", extra={"ctx": {"event": "payment_approved", "sessionId": session_id,
                                                         "attempt": attempt, "transactionId": result.transaction_id}})
            return PaymentOutcome(SessionStatus.APPROVED, True, False, "Payment approved.", session)

        retry = attempt < self.settings.max_payment_attempts
        next_status = SessionStatus.IN_PROGRESS if retry else SessionStatus.DECLINED
        updates = {"status": next_status, "decline_reason": result.decline_code,
                   "card_brand": result.brand, "card_last4": result.last4, "updated_at": self.clock()}
        if not retry:
            updates["completed_at"] = self.clock()
        session = self.repo.transition(session_id, [SessionStatus.PROCESSING], updates)
        log.info("payment declined", extra={"ctx": {"event": "payment_declined", "sessionId": session_id,
                                                     "attempt": attempt, "declineCode": result.decline_code}})
        return PaymentOutcome(next_status, False, retry, result.message or "The card was declined.", session)

    # ------------------------------------------------------------- reporting
    def metrics(self) -> dict:
        sessions = self.repo.list_recent(1000)
        by_status: dict[str, int] = {}
        for s in sessions:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
        done = [s for s in sessions if s.status.is_terminal and s.status != SessionStatus.EXPIRED]
        approved = [s for s in sessions if s.status == SessionStatus.APPROVED]
        ivr_times = [s.completed_at - s.ivr_started_at for s in sessions
                     if s.completed_at and s.ivr_started_at]
        return {
            "totalSessions": len(sessions),
            "byStatus": by_status,
            "successRate": round(len(approved) / len(done), 3) if done else None,
            "collectedCents": sum(s.amount_cents for s in approved),
            "avgIvrSeconds": round(sum(ivr_times) / len(ivr_times), 1) if ivr_times else None,
            "avgAttempts": round(sum(s.attempts for s in done) / len(done), 2) if done else None,
            "recent": [s.public_view() for s in sessions[:25]],
        }

    def _unique_code(self, now: float) -> str:
        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            if not self.repo.code_in_use(code, now):
                return code
        raise PaymentError("Could not allocate a payment code; try again.")


def _normalise_ani(ani: str | None) -> str | None:
    if not ani:
        return None
    digits = "".join(ch for ch in ani if ch.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}" if digits else None
