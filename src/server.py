"""Local end-to-end simulator (also the Vercel entrypoint when Root Directory = src).

  python local/server.py        -> http://127.0.0.1:8311

- /api/*   is served by the *real* Lambda handler (handlers.agent_api) via a
           tiny API Gateway v2 event adapter, so the demo exercises the same
           code that is deployed.
- /ivr/*   plays the payment IVR (wilmington_pay.ivr) in place of Amazon
           Connect, driven by the on-screen keypad.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("REPOSITORY", "memory")
os.environ.setdefault("PAYMENT_GATEWAY", "mock")
os.environ.setdefault("INPUT_DECRYPTION", "plaintext")
os.environ.setdefault("RINGCENTRAL_DRY_RUN", "true")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from handlers import agent_api  # noqa: E402
from wilmington_pay.container import payment_service  # noqa: E402
from wilmington_pay.ivr import IvrCall, PaymentIvr  # noqa: E402

app = FastAPI(title="CoW 311 Secure Payment POC")
STATIC = Path(__file__).parent / "static"

ROUTES = ["GET /fees", "POST /sessions", "GET /sessions/{id}", "POST /sessions/{id}/cancel", "GET /metrics"]


def _match(method: str, path: str) -> tuple[str, dict]:
    for route in ROUTES:
        r_method, r_path = route.split(" ", 1)
        pattern = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", r_path) + "$"
        m = re.match(pattern, path)
        if r_method == method and m:
            return route, m.groupdict()
    return f"{method} {path}", {}


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def api_proxy(path: str, request: Request) -> Response:
    route, params = _match(request.method, "/" + path)
    event = {
        "version": "2.0",
        "routeKey": route,
        "rawPath": "/" + path,
        "pathParameters": params or None,
        "body": (await request.body()).decode() or None,
        "requestContext": {"http": {"method": request.method},
                           "authorizer": {"jwt": {"claims": {"email": "agent.demo@wilmingtonde.gov"}}}},
    }
    result = agent_api.handler(event)
    return Response(result["body"], status_code=result["statusCode"], media_type="application/json")


# ---------------------------------------------------------------- IVR emulator
_ivr = PaymentIvr(payment_service())
_calls: dict[str, IvrCall] = {}


class StartCall(BaseModel):
    ani: str | None = None


class Input(BaseModel):
    digits: str


@app.post("/ivr/calls")
def start_call(body: StartCall) -> dict:
    call, turn = _ivr.start(body.ani)
    _calls[call.call_id] = call
    return {"callId": call.call_id, "sessionId": call.session_id, "turn": turn.to_dict()}


@app.post("/ivr/calls/{call_id}/input")
def call_input(call_id: str, body: Input) -> dict:
    call = _calls.get(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    turn = _ivr.handle(call, body.digits)
    if turn.done:
        _calls.pop(call_id, None)
    # Never echo digits back - the response only carries the next prompt.
    return {"callId": call_id, "sessionId": call.session_id, "turn": turn.to_dict()}


@app.post("/ivr/calls/{call_id}/hangup")
def hangup(call_id: str) -> dict:
    call = _calls.pop(call_id, None)
    if call:
        call.wipe()
        if call.session_id:
            s = payment_service().get_session(call.session_id)
            if s and not s.status.is_terminal:
                payment_service().cancel_session(call.session_id, "caller_hung_up")
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8311"))
    print(f"\n  City of Wilmington 311 Secure Payment POC -> http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
