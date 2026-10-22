# City of Wilmington 311: Secure In-Call Payment (POC)

A proof of concept for the Accelance proposal *"AI-Enabled Secure Payment Solution for 311 Special Trash Pickup"*.
A 311 agent transfers the caller to an automated payment line. The resident keys in their card details by phone keypad (DTMF), and the card is charged through a payment gateway. The caller then goes back to the agent with the result. **No card number, expiry or CVV is ever stored or logged.**

- Architecture, decisions, PCI scope and deck review: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Amazon Connect flow and Lex bot build spec: [docs/CONNECT_FLOW.md](docs/CONNECT_FLOW.md)

## Run the demo locally (about 2 minutes, no AWS needed)

```powershell
pip install -r requirements-dev.txt
python local/server.py              # http://127.0.0.1:8311
```

The page has three panels:

1. **Agent console.** Pick pickup items. The total is priced on the server. Click **Transfer caller to secure payment**.
2. **Resident's phone.** The call connects to the payment line automatically. Use the keypad (or your keyboard; Enter = `#`):
   `1` → `4000000000000002` → `1229` → `123` → `19801` → `1` (declined, so try another card)
   → `4111111111111111` → `1229` → `123` → `19801` → `1` (approved).
   Card entries show as `•••`. Tick *Speak prompts aloud* to hear the voice prompts.
3. **Ops dashboard.** Success rate, amount collected, average time in the payment line, and recent sessions (last4 only).

The agent panel updates live: `PENDING → IN PROGRESS → APPROVED`, with card ending, auth code and transaction ID.
To try the other paths, use the test cards listed on the page: insufficient funds, processor timeout (goes to manual review), `*` to return to the agent, and a wrong payment code.

## Tests

```powershell
python -m pytest -q        # 41 tests: card rules, redaction, double-charge lock, IVR dialog, Lambda handlers
```

## Project layout

```
src/
  wilmington_pay/          domain + application layer (no AWS imports at module load)
    service.py             business rules: pricing, session lifecycle, charge, retries, metrics
    ivr.py                 DTMF payment dialog (the executable spec of the Connect flow)
    card.py                Luhn / brand / expiry / CVV validation; CardData masks itself
    redaction.py           JSON logging with PAN redaction safety net
    repository.py          in-memory + DynamoDB (compare-and-set status transitions)
    gateway/               port + mock + Authorize.Net sandbox adapter
    connect_crypto.py      decrypts Amazon Connect encrypted DTMF input
    ringcentral.py         Call Control transfer (JWT auth, dry-run by default)
  handlers/                Lambda entry points
    agent_api.py           API Gateway HTTP API for the agent console
    connect_payment.py     Amazon Connect flow Lambda (lookup / charge / cancel)
    stream_archiver.py     DynamoDB Streams → S3 for analytics
  server.py                FastAPI simulator (Vercel entrypoint); /api/* calls the real agent_api handler
local/server.py            launcher for src/server.py
app.py                     Vercel entrypoint when Root Directory is the repo root
infra/template.yaml        AWS SAM: KMS, DynamoDB, S3, HTTP API + JWT, Lambdas, Connect wiring, alarm
tests/
docs/
```

## Deploy to AWS (dev)

Prerequisites: AWS SAM CLI, an Amazon Connect instance with a claimed DID, and an OIDC app for agent login.

```powershell
# 1. Secrets
aws secretsmanager create-secret --name cow311/connect-private-key --secret-string file://connect_private.pem
aws secretsmanager create-secret --name cow311/authorizenet --secret-string '{"api_login_id":"...","transaction_key":"..."}'

# 2. Build and deploy
sam build -t infra/template.yaml --use-container
sam deploy --guided --stack-name cow311-payments-dev --capabilities CAPABILITY_IAM

# 3. Build the Connect flow and Lex bot from docs/CONNECT_FLOW.md and point the DID at the flow
```

Configuration is through environment variables (see `src/wilmington_pay/config.py`). Settings that matter:
`PAYMENT_GATEWAY` (`mock` | `authorizenet`), `INPUT_DECRYPTION` (`connect` in AWS), and `RINGCENTRAL_DRY_RUN`.
With `APP_ENV=prod`, the service refuses to start if the gateway is `mock` or input decryption is `plaintext`.

## What the POC proves, and what it doesn't

| Proven here | Needs the City's environments |
|---|---|
| End-to-end dialog, retries, `*` to agent, timeouts | Real PSTN transfer RingCentral ⇄ Connect, and ANI behaviour on transfer |
| Server-side pricing, one-time code, 15-minute expiry | Real fee schedule from Public Works |
| No-double-charge lock, idempotency, safe handling of unknown outcomes | City's chosen payment processor |
| No card data in storage, logs or API responses (tested) | QSA review of the CDE boundary |
| Infra as code that passes cfn-lint | Deployed and load-tested stack |
