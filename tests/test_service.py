import pytest
from conftest import ANI, APPROVE, DECLINE, TIMEOUT

from wilmington_pay.card import build_card
from wilmington_pay.models import SessionStatus
from wilmington_pay.service import PaymentError


def card(pan=APPROVE, now=1_790_000_000.0):
    return build_card(pan, "1229", "123", "19801", now)


def test_amount_is_priced_server_side(svc):
    s = svc.create_session("SR-1", [{"code": "BULK_FURNITURE", "quantity": 2},
                                    {"code": "APPLIANCE_FREON", "quantity": 1}], "agent")
    assert s.amount_cents == 2 * 2500 + 5000
    assert len(s.payment_code) == 6 and s.status == SessionStatus.PENDING


@pytest.mark.parametrize("items", [[], [{"code": "NOPE", "quantity": 1}],
                                   [{"code": "TIRES", "quantity": 0}], [{"code": "TIRES", "quantity": 99}]])
def test_rejects_bad_items(svc, items):
    with pytest.raises(PaymentError):
        svc.create_session("SR-1", items, "agent")


def test_resolve_by_ani_and_code(svc, session):
    assert svc.resolve_for_caller(ani="(302) 555-0123").session_id == session.session_id
    assert svc.resolve_for_caller(code=session.payment_code).session_id == session.session_id
    assert svc.resolve_for_caller(code="000000") is None or session.payment_code == "000000"


def test_approved_payment_stores_only_safe_fields(svc, session):
    out = svc.process_payment(session.session_id, card())
    assert out.approved and out.session.status == SessionStatus.APPROVED
    item = svc.repo._items[session.session_id]
    flat = str(item)
    assert APPROVE not in flat and "1229" not in flat
    assert item["card_last4"] == "1111" and item["gateway_transaction_id"].startswith("MOCK-")


def test_cannot_charge_twice(svc, session):
    svc.process_payment(session.session_id, card())
    with pytest.raises(PaymentError):
        svc.process_payment(session.session_id, card())


def test_decline_allows_retry_then_final_decline(svc, session):
    for attempt in (1, 2):
        out = svc.process_payment(session.session_id, card(DECLINE))
        assert out.retry_allowed and out.session.status == SessionStatus.IN_PROGRESS
        assert out.session.attempts == attempt
    out = svc.process_payment(session.session_id, card(DECLINE))
    assert not out.retry_allowed and out.session.status == SessionStatus.DECLINED


def test_decline_then_approve(svc, session):
    svc.process_payment(session.session_id, card(DECLINE))
    out = svc.process_payment(session.session_id, card())
    assert out.approved and out.session.attempts == 2


def test_gateway_error_goes_to_manual_review_not_retry(svc, session):
    out = svc.process_payment(session.session_id, card(TIMEOUT))
    assert out.status == SessionStatus.FAILED and not out.retry_allowed
    with pytest.raises(PaymentError):
        svc.process_payment(session.session_id, card())


def test_expired_session(svc, session, clock):
    clock.t += svc.settings.session_ttl_seconds + 1
    assert svc.resolve_for_caller(ani=ANI) is None
    with pytest.raises(PaymentError):
        svc.process_payment(session.session_id, card(now=clock.t))
    assert svc.get_session(session.session_id).status == SessionStatus.EXPIRED


def test_metrics(svc, session):
    svc.mark_ivr_started(session.session_id)
    svc.process_payment(session.session_id, card())
    m = svc.metrics()
    assert m["successRate"] == 1.0 and m["collectedCents"] == 5000 and m["byStatus"] == {"APPROVED": 1}
