from conftest import AMEX, ANI, APPROVE, DECLINE

from wilmington_pay.ivr import PaymentIvr, Step
from wilmington_pay.models import SessionStatus


def run(ivr, call, *entries):
    turn = None
    for e in entries:
        turn = ivr.handle(call, e)
    return turn


def test_happy_path_by_caller_id(svc, session):
    ivr = PaymentIvr(svc)
    call, turn = ivr.start(ANI)
    assert turn.step == Step.CONFIRM_AMOUNT and "50 dollars" in turn.prompt
    turn = ivr.handle(call, "1")
    assert turn.step == Step.CARD_NUMBER and turn.sensitive
    turn = run(ivr, call, APPROVE, "1229", "123", "19801")
    assert turn.step == Step.REVIEW and "1 1 1 1" in turn.prompt
    turn = ivr.handle(call, "1")
    assert turn.done and turn.transfer_to_agent and turn.outcome == "APPROVED"
    assert call._pan == call._cvv == call._exp == ""          # wiped
    assert svc.get_session(session.session_id).status == SessionStatus.APPROVED


def test_unknown_ani_falls_back_to_payment_code(svc, session):
    ivr = PaymentIvr(svc)
    call, turn = ivr.start("+12155550000")
    assert turn.step == Step.IDENTIFY
    turn = ivr.handle(call, session.payment_code)
    assert turn.step == Step.CONFIRM_AMOUNT


def test_bad_code_three_times_returns_to_agent(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(None)
    bad = "999999" if session.payment_code != "999999" else "888888"
    turn = run(ivr, call, bad, bad, bad)
    assert turn.done and turn.outcome == "NOT_FOUND"


def test_invalid_card_reprompts_without_echo(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(ANI)
    ivr.handle(call, "1")
    turn = ivr.handle(call, "4111111111111112")
    assert turn.step == Step.CARD_NUMBER and "try again" in turn.prompt and "4111" not in turn.prompt


def test_amex_asks_for_four_digit_code(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(ANI)
    turn = run(ivr, call, "1", AMEX, "1229")
    assert "4 digit" in turn.prompt


def test_decline_then_new_card_succeeds(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(ANI)
    turn = run(ivr, call, "1", DECLINE, "1229", "123", "19801", "1")
    assert turn.step == Step.CARD_NUMBER and "another card" in turn.prompt
    turn = run(ivr, call, APPROVE, "1229", "123", "19801", "1")
    assert turn.outcome == "APPROVED"
    assert svc.get_session(session.session_id).attempts == 2


def test_star_returns_to_agent_and_cancels(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(ANI)
    turn = run(ivr, call, "1", "*")
    assert turn.done and turn.outcome == "ABANDONED"
    assert svc.get_session(session.session_id).status == SessionStatus.CANCELLED


def test_review_reenter_wipes_card(svc, session):
    ivr = PaymentIvr(svc)
    call, _ = ivr.start(ANI)
    turn = run(ivr, call, "1", APPROVE, "1229", "123", "19801", "2")
    assert turn.step == Step.CARD_NUMBER and call._pan == ""
