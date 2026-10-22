"""RingCentral Call Control client - moves the live caller to the payment IVR.

Flow: the agent clicks "Take payment" in the console -> backend creates the
payment session -> this client blind-transfers the caller's party in the
active telephony session to the Amazon Connect payment DID. The agent drops
off the audio path, so they never hear the card digits (PCI DSS scope
reduction). Connect transfers the caller back to the 311 queue afterwards.

Auth uses the JWT bearer grant (server-to-server app, no user login).
API docs: https://developers.ringcentral.com/api-reference/Call-Control
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import Settings
from .redaction import get_logger
from .secret_store import get_secret_json

log = get_logger(__name__)


@dataclass
class TransferResult:
    dry_run: bool
    target: str
    response: dict | None = None


class RingCentralClient:
    def __init__(self, server: str, client_id: str, client_secret: str, jwt: str, dry_run: bool = True) -> None:
        self._server = server.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._jwt = jwt
        self._dry_run = dry_run
        self._token: str | None = None
        self._token_exp = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RingCentralClient":
        if settings.ringcentral_dry_run:
            return cls(settings.ringcentral_server, "", "", "", dry_run=True)
        c = get_secret_json(settings.ringcentral_secret_arn, env_prefix="RINGCENTRAL_")
        return cls(settings.ringcentral_server, c["client_id"], c["client_secret"], c["jwt"], dry_run=False)

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": self._jwt}).encode()
        req = urllib.request.Request(f"{self._server}/restapi/oauth/token", data=body, method="POST", headers={
            "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        self._token, self._token_exp = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def transfer_to_number(self, telephony_session_id: str, party_id: str, phone_number: str) -> TransferResult:
        path = (f"/restapi/v1.0/account/~/telephony/sessions/{urllib.parse.quote(telephony_session_id)}"
                f"/parties/{urllib.parse.quote(party_id)}/transfer")
        if self._dry_run:
            log.info("ringcentral transfer (dry run)", extra={"ctx": {
                "event": "rc_transfer_dry_run", "path": path, "target": phone_number}})
            return TransferResult(dry_run=True, target=phone_number)
        req = urllib.request.Request(
            f"{self._server}{path}", data=json.dumps({"phoneNumber": phone_number}).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return TransferResult(dry_run=False, target=phone_number, response=json.loads(resp.read() or b"{}"))
