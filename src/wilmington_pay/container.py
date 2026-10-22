"""Composition root - wires settings, repository, gateway and service once per
Lambda container (warm invocations reuse them)."""
from __future__ import annotations

from functools import lru_cache

from .config import get_settings
from .gateway import build_gateway
from .repository import build_repository
from .service import PaymentService


@lru_cache(maxsize=1)
def payment_service() -> PaymentService:
    settings = get_settings()
    return PaymentService(build_repository(settings), build_gateway(settings), settings)
