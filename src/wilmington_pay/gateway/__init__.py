from __future__ import annotations

from ..config import Settings
from .base import ChargeRequest, ChargeResult, GatewayError, PaymentGateway


def build_gateway(settings: Settings) -> PaymentGateway:
    if settings.gateway == "mock":
        from .mock import MockGateway

        return MockGateway()
    if settings.gateway == "authorizenet":
        from .authorizenet import AuthorizeNetGateway

        return AuthorizeNetGateway.from_settings(settings)
    raise ValueError(f"Unknown PAYMENT_GATEWAY {settings.gateway!r}")


__all__ = ["ChargeRequest", "ChargeResult", "GatewayError", "PaymentGateway", "build_gateway"]
