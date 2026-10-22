import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.update({
    "APP_ENV": "test",
    "REPOSITORY": "memory",
    "PAYMENT_GATEWAY": "mock",
    "INPUT_DECRYPTION": "plaintext",
    "RINGCENTRAL_DRY_RUN": "true",
})

from wilmington_pay import repository  # noqa: E402
from wilmington_pay.config import Settings  # noqa: E402
from wilmington_pay.container import payment_service  # noqa: E402
from wilmington_pay.gateway.mock import MockGateway  # noqa: E402
from wilmington_pay.repository import InMemorySessionRepository  # noqa: E402
from wilmington_pay.service import PaymentService  # noqa: E402

APPROVE = "4111111111111111"
DECLINE = "4000000000000002"
TIMEOUT = "4000000000000119"
AMEX = "378282246310005"
ANI = "+13025550123"


class Clock:
    def __init__(self, t: float = 1_790_000_000.0):  # 2026-09
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(clock):
    return PaymentService(InMemorySessionRepository(), MockGateway(), Settings(), clock=clock)


@pytest.fixture
def session(svc):
    return svc.create_session("SR-2026-100001", [{"code": "BULK_FURNITURE", "quantity": 2}], "agent-1", ANI)


@pytest.fixture(autouse=True)
def fresh_container():
    payment_service.cache_clear()
    repository._cached = None
    yield
    payment_service.cache_clear()
    repository._cached = None
