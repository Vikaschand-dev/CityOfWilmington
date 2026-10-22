"""Authorize.Net (sandbox) adapter - an example of a real processor adapter.

Chosen for the POC because its sandbox accepts server-side card data with a
free developer account. Swap for the City's processor once selected.
NOTE: written against the public createTransactionRequest JSON API; verify
against a live sandbox account before relying on it.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import Settings
from ..secret_store import get_secret_json
from .base import ChargeRequest, ChargeResult, GatewayError


class AuthorizeNetGateway:
    name = "authorizenet"

    def __init__(self, api_login_id: str, transaction_key: str, endpoint: str, timeout: float = 15.0) -> None:
        self._login = api_login_id
        self._key = transaction_key
        self._endpoint = endpoint
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "AuthorizeNetGateway":
        creds = get_secret_json(settings.gateway_secret_arn, env_prefix="AUTHORIZENET_")
        return cls(creds["api_login_id"], creds["transaction_key"], settings.authorizenet_endpoint)

    def _payload(self, req: ChargeRequest) -> dict:
        card = req.card
        tx = {
            "transactionType": "authCaptureTransaction",
            "amount": f"{req.amount_cents / 100:.2f}",
            "payment": {
                "creditCard": {
                    "cardNumber": card.pan,
                    "expirationDate": f"{card.exp_year:04d}-{card.exp_month:02d}",
                    "cardCode": card.cvv,
                }
            },
            "order": {"invoiceNumber": req.reference[:20], "description": req.description[:255]},
        }
        if card.postal_code:
            tx["billTo"] = {"zip": card.postal_code}
        return {
            "createTransactionRequest": {
                "merchantAuthentication": {"name": self._login, "transactionKey": self._key},
                # refId doubles as our idempotency correlation id
                "refId": req.idempotency_key[:20],
                "transactionRequest": tx,
            }
        }

    def charge(self, request: ChargeRequest) -> ChargeResult:
        body = json.dumps(self._payload(request)).encode()
        http_req = urllib.request.Request(
            self._endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(http_req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8-sig")  # API prefixes a BOM
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GatewayError(f"Authorize.Net unreachable: {type(exc).__name__}") from None
        finally:
            del body  # drop the serialized card data promptly

        data = json.loads(raw)
        tr = data.get("transactionResponse") or {}
        approved = tr.get("responseCode") == "1"
        if not approved and not tr:
            msg = (data.get("messages", {}).get("message") or [{}])[0].get("text", "Gateway error")
            raise GatewayError(msg)
        errors = tr.get("errors") or [{}]
        return ChargeResult(
            approved=approved,
            transaction_id=tr.get("transId") or None,
            auth_code=tr.get("authCode") or None,
            last4=request.card.last4,
            brand=request.card.brand,
            decline_code=None if approved else str(errors[0].get("errorCode", tr.get("responseCode"))),
            message=None if approved else errors[0].get("errorText", "Declined"),
        )
