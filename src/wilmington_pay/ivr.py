"""DTMF payment dialog - executable specification of the Amazon Connect flow.

In AWS, these steps are Connect flow blocks ("Store customer input" with
encryption for the card fields, a Lex bot for the conversational turns) and
the Lambda only performs lookup + charge (handlers/connect_payment.py).

Locally, this state machine plays the same dialog so the whole call can be
demoed and tested end-to-end without telephony. The prompts here are the
copy that goes into the Connect flow / Polly.

Card digits live only on the in-memory IvrCall object, are passed straight
to PaymentService.process_payment, and are wiped immediately afterwards.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from .card import CardValidationError, build_card, detect_brand, parse_expiry, validate_cvv, validate_pan, validate_postal
from .models import SessionStatus
from .service import PaymentError, PaymentService


class Step(str, Enum):
    IDENTIFY = "IDENTIFY"
    CONFIRM_AMOUNT = "CONFIRM_AMOUNT"
    CARD_NUMBER = "CARD_NUMBER"
    EXPIRY = "EXPIRY"
    CVV = "CVV"
    POSTAL = "POSTAL"
    REVIEW = "REVIEW"
    DONE = "DONE"


SENSITIVE_STEPS = {Step.CARD_NUMBER, Step.EXPIRY, Step.CVV}

AGENT_HINT = " Press star at any time to return to a City representative."


@dataclass
class IvrTurn:
    prompt: str
    step: Step
    sensitive: bool = False          # UI/telephony must mask what the caller types
    done: bool = False
    transfer_to_agent: bool = False
    outcome: str | None = None       # APPROVED / DECLINED / FAILED / ABANDONED / NOT_FOUND

    def to_dict(self) -> dict:
        return {"prompt": self.prompt, "step": self.step.value, "sensitive": self.sensitive,
                "done": self.done, "transferToAgent": self.transfer_to_agent, "outcome": self.outcome}


@dataclass
class IvrCall:
    call_id: str
    ani: str | None
    step: Step = Step.IDENTIFY
    session_id: str | None = None
    retries: int = 0
    # Sensitive, in-memory only. repr=False keeps them out of any debug output.
    _pan: str = field(default="", repr=False)
    _exp: str = field(default="", repr=False)
    _cvv: str = field(default="", repr=False)
    _zip: str = field(default="", repr=False)   # not cardholder data, but no reason to keep it

    def wipe(self) -> None:
        self._pan = self._exp = self._cvv = self._zip = ""


def _sorry(message: str) -> str:
    return "Sorry, " + (message[:1].lower() + message[1:] if message else "")


def _say_amount(cents: int) -> str:
    dollars, c = divmod(cents, 100)
    return f"{dollars} dollars" + (f" and {c} cents" if c else "")


class PaymentIvr:
    def __init__(self, service: PaymentService) -> None:
        self.service = service
        self.max_retries = service.settings.max_input_retries

    # ------------------------------------------------------------------ API
    def start(self, ani: str | None) -> tuple[IvrCall, IvrTurn]:
        call = IvrCall(call_id=str(uuid.uuid4()), ani=ani)
        greeting = "Welcome to the City of Wilmington secure payment line. "
        session = self.service.resolve_for_caller(ani=ani) if ani else None
        if session:
            call.session_id = session.session_id
            self.service.mark_ivr_started(session.session_id)
            return call, self._confirm_amount(call, greeting)
        call.step = Step.IDENTIFY
        return call, IvrTurn(greeting + "Please enter the six digit payment code your representative "
                             "gave you, followed by the pound key." + AGENT_HINT, Step.IDENTIFY)

    def handle(self, call: IvrCall, digits: str) -> IvrTurn:
        """`digits` is one completed DTMF entry (terminator '#' already stripped)."""
        digits = digits.strip()
        if call.step == Step.DONE:
            return IvrTurn("This call has ended.", Step.DONE, done=True)
        if digits == "*":
            return self._back_to_agent(call, "Transferring you back to a City representative.", "ABANDONED")
        try:
            return getattr(self, f"_on_{call.step.value.lower()}")(call, digits)
        except CardValidationError as exc:
            return self._retry(call, str(exc))

    # --------------------------------------------------------------- steps
    def _on_identify(self, call: IvrCall, digits: str) -> IvrTurn:
        session = self.service.resolve_for_caller(code=digits) if len(digits) == 6 else None
        if not session:
            return self._retry(call, "We could not find a payment with that code.", not_found=True)
        call.session_id = session.session_id
        call.retries = 0
        self.service.mark_ivr_started(session.session_id)
        return self._confirm_amount(call, "")

    def _confirm_amount(self, call: IvrCall, prefix: str) -> IvrTurn:
        s = self.service.get_session(call.session_id)
        call.step = Step.CONFIRM_AMOUNT
        return IvrTurn(f"{prefix}You are paying {_say_amount(s.amount_cents)} for special trash pickup, "
                       f"request number {' '.join(s.service_request_id[-6:])}. "
                       "Press 1 to continue, or 2 to return to the representative.", Step.CONFIRM_AMOUNT)

    def _on_confirm_amount(self, call: IvrCall, digits: str) -> IvrTurn:
        if digits == "1":
            return self._ask_card(call, "")
        if digits == "2":
            self.service.cancel_session(call.session_id, "caller_declined_amount")
            return self._back_to_agent(call, "No problem. Transferring you back to the representative.", "ABANDONED")
        return self._retry(call, "Please press 1 to continue or 2 to return to the representative.")

    def _ask_card(self, call: IvrCall, prefix: str) -> IvrTurn:
        call.step, call.retries = Step.CARD_NUMBER, 0
        return IvrTurn(prefix + "Using your telephone keypad, enter your card number followed by the pound key. "
                       "Your entry is secure and will not be heard by anyone.", Step.CARD_NUMBER, sensitive=True)

    def _on_card_number(self, call: IvrCall, digits: str) -> IvrTurn:
        call._pan = validate_pan(digits)
        call.step, call.retries = Step.EXPIRY, 0
        return IvrTurn("Enter the four digit expiration date, two digits for the month and two for the year.",
                       Step.EXPIRY, sensitive=True)

    def _on_expiry(self, call: IvrCall, digits: str) -> IvrTurn:
        parse_expiry(digits, self.service.clock())
        call._exp = digits
        call.step, call.retries = Step.CVV, 0
        n = 4 if detect_brand(call._pan) == "AMEX" else 3
        where = "on the front" if n == 4 else "on the back"
        return IvrTurn(f"Enter the {n} digit security code {where} of your card.", Step.CVV, sensitive=True)

    def _on_cvv(self, call: IvrCall, digits: str) -> IvrTurn:
        validate_cvv(digits, detect_brand(call._pan))
        call._cvv = digits
        call.step, call.retries = Step.POSTAL, 0
        return IvrTurn("Enter the five digit billing ZIP code for the card.", Step.POSTAL)

    def _on_postal(self, call: IvrCall, digits: str) -> IvrTurn:
        validate_postal(digits)
        call.step, call.retries = Step.REVIEW, 0
        call._zip = digits
        s = self.service.get_session(call.session_id)
        return IvrTurn(f"To confirm: charge {_say_amount(s.amount_cents)} to the card ending in "
                       f"{' '.join(call._pan[-4:])}. Press 1 to pay now, or 2 to re-enter your card.", Step.REVIEW)

    def _on_review(self, call: IvrCall, digits: str) -> IvrTurn:
        if digits == "2":
            call.wipe()
            return self._ask_card(call, "")
        if digits != "1":
            return self._retry(call, "Press 1 to pay now, or 2 to re-enter your card.")
        try:
            card = build_card(call._pan, call._exp, call._cvv, call._zip or None, self.service.clock())
            outcome = self.service.process_payment(call.session_id, card)
        except PaymentError as exc:
            return self._back_to_agent(call, f"{exc} Transferring you back to the representative.", "FAILED")
        finally:
            call.wipe()
            card = None  # noqa: F841 - release reference

        if outcome.approved:
            s = outcome.session
            return self._back_to_agent(
                call, f"Thank you. Your payment of {_say_amount(s.amount_cents)} was approved. "
                      f"Your confirmation number is {' '.join(s.auth_code or '')}. "
                      "Transferring you back to the representative to complete your request.", "APPROVED")
        if outcome.retry_allowed:
            return self._ask_card(call, f"{_sorry(outcome.message)} Let's try another card. ")
        result = "FAILED" if outcome.status == SessionStatus.FAILED else "DECLINED"
        return self._back_to_agent(call, f"{_sorry(outcome.message)} Transferring you back to the representative.",
                                   result)

    # -------------------------------------------------------------- helpers
    def _retry(self, call: IvrCall, message: str, not_found: bool = False) -> IvrTurn:
        call.retries += 1
        if call.retries >= self.max_retries:
            if call.session_id:
                self.service.cancel_session(call.session_id, f"max_retries_{call.step.value.lower()}")
            return self._back_to_agent(call, "We were not able to complete your payment. "
                                       "Transferring you back to the representative.",
                                       "NOT_FOUND" if not_found else "ABANDONED")
        return IvrTurn(f"{message} Please try again.", call.step, sensitive=call.step in SENSITIVE_STEPS)

    def _back_to_agent(self, call: IvrCall, prompt: str, outcome: str) -> IvrTurn:
        call.wipe()
        if outcome == "ABANDONED" and call.session_id:
            s = self.service.get_session(call.session_id)
            if s and not s.status.is_terminal:
                self.service.cancel_session(call.session_id, "caller_requested_agent")
        call.step = Step.DONE
        return IvrTurn(prompt, Step.DONE, done=True, transfer_to_agent=True, outcome=outcome)
