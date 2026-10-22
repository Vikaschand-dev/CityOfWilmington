"""Agent-facing REST API (API Gateway HTTP API, payload v2.0).

Called by the agent console embedded in RingCentral (or the 311 CRM).
Secured by a JWT authorizer (City IdP / Cognito) - see infra/template.yaml.

  GET  /fees                       fee schedule for the agent UI
  POST /sessions                   create payment session (+ transfer caller)
  GET  /sessions/{id}              poll status after the caller returns
  POST /sessions/{id}/cancel       agent cancels
  GET  /metrics                    operational KPIs
"""
from __future__ import annotations

import json

from wilmington_pay.config import get_settings
from wilmington_pay.container import payment_service
from wilmington_pay.redaction import get_logger
from wilmington_pay.ringcentral import RingCentralClient
from wilmington_pay.service import PaymentError

log = get_logger(__name__)


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def _agent_id(event: dict) -> str:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    return claims.get("email") or claims.get("sub") or "local-agent"


def handler(event: dict, context=None) -> dict:
    svc = payment_service()
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    route = event.get("routeKey", "")
    params = event.get("pathParameters") or {}
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})

    try:
        if route == "GET /fees":
            return _resp(200, {"items": [{"code": k, **v} for k, v in svc.settings.fee_schedule.items()]})

        if route == "POST /sessions":
            session = svc.create_session(
                service_request_id=str(body.get("serviceRequestId", "")).strip(),
                items=body.get("items") or [],
                agent_id=_agent_id(event),
                caller_ani=body.get("callerAni"),
            )
            transfer = None
            if body.get("telephonySessionId") and body.get("partyId"):
                settings = get_settings()
                rc = RingCentralClient.from_settings(settings)
                result = rc.transfer_to_number(body["telephonySessionId"], body["partyId"],
                                               settings.payment_ivr_number)
                transfer = {"dryRun": result.dry_run, "target": result.target}
            return _resp(201, {"session": session.public_view(), "transfer": transfer})

        if route == "GET /sessions/{id}":
            session = svc.get_session(params.get("id", ""))
            return _resp(200, {"session": session.public_view()}) if session else _resp(404, {"error": "Not found"})

        if route == "POST /sessions/{id}/cancel":
            session = svc.cancel_session(params.get("id", ""), "agent_cancelled")
            if not session:
                return _resp(409, {"error": "Session is not active"})
            return _resp(200, {"session": session.public_view()})

        if route == "GET /metrics":
            return _resp(200, svc.metrics())
    except PaymentError as exc:
        return _resp(422, {"error": str(exc)})
    except Exception:
        log.exception("agent api failure", extra={"ctx": {"route": route, "method": method}})
        return _resp(500, {"error": "Internal error"})

    return _resp(404, {"error": f"No route {route}"})
