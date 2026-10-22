"""Runtime configuration, read once from environment variables.

Every value has a safe local default so the POC runs with zero setup.
Production values come from the SAM template / Secrets Manager.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# PLACEHOLDER fee schedule (cents). The real special-pickup fees must be
# confirmed with City Public Works during Discovery (deck slide 25).
# Amounts are always computed server-side from this table; the agent UI
# never sends a free-form amount.
DEFAULT_FEE_SCHEDULE: dict[str, dict] = {
    "BULK_FURNITURE": {"label": "Bulk furniture item", "unit_cents": 2500},
    "APPLIANCE": {"label": "Appliance (non-freon)", "unit_cents": 3500},
    "APPLIANCE_FREON": {"label": "Appliance with freon", "unit_cents": 5000},
    "MATTRESS": {"label": "Mattress / box spring", "unit_cents": 2000},
    "TIRES": {"label": "Tire (each, max 4)", "unit_cents": 1000},
    "YARD_WASTE_LOAD": {"label": "Yard waste load", "unit_cents": 3000},
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    environment: str = field(default_factory=lambda: _env("APP_ENV", "local"))

    # Session lifecycle
    session_ttl_seconds: int = field(default_factory=lambda: _env_int("SESSION_TTL_SECONDS", 900))
    max_payment_attempts: int = field(default_factory=lambda: _env_int("MAX_PAYMENT_ATTEMPTS", 3))
    max_input_retries: int = field(default_factory=lambda: _env_int("MAX_INPUT_RETRIES", 3))
    max_amount_cents: int = field(default_factory=lambda: _env_int("MAX_AMOUNT_CENTS", 50000))
    record_retention_days: int = field(default_factory=lambda: _env_int("RECORD_RETENTION_DAYS", 90))

    # Persistence: "memory" (local) or "dynamodb"
    repository: str = field(default_factory=lambda: _env("REPOSITORY", "memory"))
    table_name: str = field(default_factory=lambda: _env("TABLE_NAME", "cow311-payment-sessions"))

    # Gateway: "mock" or "authorizenet"
    gateway: str = field(default_factory=lambda: _env("PAYMENT_GATEWAY", "mock"))
    gateway_secret_arn: str = field(default_factory=lambda: _env("GATEWAY_SECRET_ARN", ""))
    authorizenet_endpoint: str = field(
        default_factory=lambda: _env("AUTHORIZENET_ENDPOINT", "https://apitest.authorize.net/xml/v1/request.api")
    )

    # Amazon Connect encrypted-input decryption: "connect" or "plaintext" (local/test only)
    input_decryption: str = field(default_factory=lambda: _env("INPUT_DECRYPTION", "plaintext"))
    connect_private_key_secret_arn: str = field(default_factory=lambda: _env("CONNECT_PRIVATE_KEY_SECRET_ARN", ""))
    connect_certificate_id: str = field(default_factory=lambda: _env("CONNECT_CERTIFICATE_ID", ""))

    # Telephony
    payment_ivr_number: str = field(default_factory=lambda: _env("PAYMENT_IVR_NUMBER", "+13025550199"))
    agent_return_number: str = field(default_factory=lambda: _env("AGENT_RETURN_NUMBER", "+13025550311"))
    ringcentral_server: str = field(default_factory=lambda: _env("RINGCENTRAL_SERVER", "https://platform.devtest.ringcentral.com"))
    ringcentral_secret_arn: str = field(default_factory=lambda: _env("RINGCENTRAL_SECRET_ARN", ""))
    ringcentral_dry_run: bool = field(default_factory=lambda: _env_bool("RINGCENTRAL_DRY_RUN", True))

    fee_schedule: dict = field(default_factory=lambda: DEFAULT_FEE_SCHEDULE)

    def __post_init__(self) -> None:
        # Hard guard: plaintext card input is never acceptable outside local/test.
        if self.environment == "prod" and self.input_decryption != "connect":
            raise RuntimeError("INPUT_DECRYPTION must be 'connect' in prod")
        if self.environment == "prod" and self.gateway == "mock":
            raise RuntimeError("PAYMENT_GATEWAY=mock is not allowed in prod")


def get_settings() -> Settings:
    return Settings()
