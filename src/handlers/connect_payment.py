"""Amazon Connect contact-flow Lambda (the "AI payment agent" back end).

Invoked from the payment flow via "Invoke AWS Lambda function" blocks.
Connect requires a flat map of string values as the response.

Parameters.action:
  lookup  -> find the pending session by caller ANI, or by paymentCode
  charge  -> decrypt the encrypted DTMF entries and charge the card
  cancel  -> caller pressed * / hung up / declined the amount

The card fields arrive as ciphertext (Connect "Store customer input" with
encryption). They are decrypted only here, held in local variables for the
duration of one gateway call, and never logged or persisted.
"""
from __future__ import annotations

from wilmington_pay.card import CardValidationError, build_card
from wilmington_pay.config import get_settings
from wilmington_pay.connect_crypto import build_decryptor
from wilmington_pay.container import payment_service
from wilmington_pay.models import SessionStatus
from wilmington_pay.redaction import get_logger, redact_mapping
from wilmington_pay.service import PaymentError

log = get_logger(__name__)


def _spoken_amount(cents: int) -> str:
    dollars, c = divmod(cents, 100)
    return f"{dollars} dollars" + (f" and {c} cents" if c else "")


def handler(event: dict, context=None) -> dict[str, str]:
    details = event.get("Details", {})
    params = details.get("Parameters", {}) or {}
    contact = details.get("ContactData", {}) or {}
    action = params.get("action", "")
    contact_id = contact.get("ContactId", "")
    log.info("connect invoke", extra={"ctx": {"action": action, "contactId": contact_id,
                                              "params": redact_mapping(params)}})
    try:
        if action == "lookup":
            return _lookup(params, contact)
        if action == "charge":
            return _charge(params)
        if action == "cancel":
            payment_service().cancel_session(params.get("sessionId", ""), params.get("reason", "caller_abandoned"))
            return {"result": "CANCELLED"}
    except Exception:
        log.exception("connect handler failure", extra={"ctx": {"action": action, "contactId": contact_id}})
        return {"result": "ERROR"}
    return {"result": "ERROR", "message": f"unknown action {action!r}"}


def _lookup(params: dict, contact: dict) -> dict[str, str]:
    svc = payment_service()
    code = params.get("paymentCode")
    ani = (contact.get("CustomerEndpoint") or {}).get("Address")
    session = svc.resolve_for_caller(code=code) if code else svc.resolve_for_caller(ani=ani)
    if not session:
        return {"found": "false"}
    svc.mark_ivr_started(session.session_id)
    return {
        "found": "true",
        "sessionId": session.session_id,
        "amountCents": str(session.amount_cents),
        "amountSpoken": _spoken_amount(session.amount_cents),
        "serviceRequestId": session.service_request_id,
    }


def _charge(params: dict) -> dict[str, str]:
    svc = payment_service()
    dec = build_decryptor(get_settings())
    session_id = params.get("sessionId", "")
    try:
        card = build_card(
            pan=dec.decrypt(params.get("encCardNumber", "")),
            mmyy=dec.decrypt(params.get("encExpiry", "")),
            cvv=dec.decrypt(params.get("encCvv", "")),
            postal=params.get("postalCode") or None,
            now=svc.clock(),
        )
    except CardValidationError as exc:
        return {"result": "INVALID_CARD", "message": str(exc)}

    try:
        outcome = svc.process_payment(session_id, card)
    except PaymentError as exc:
        return {"result": "ERROR", "message": str(exc)}
    finally:
        del card

    s = outcome.session
    if outcome.approved:
        return {"result": "APPROVED", "authCode": s.auth_code or "", "last4": s.card_last4 or "",
                "transactionId": s.gateway_transaction_id or ""}
    if outcome.retry_allowed:
        return {"result": "RETRY", "message": outcome.message}
    return {"result": "FAILED" if outcome.status == SessionStatus.FAILED else "DECLINED",
            "message": outcome.message}
