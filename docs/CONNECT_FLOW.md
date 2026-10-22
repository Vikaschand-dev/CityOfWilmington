# Amazon Connect payment flow and Lex bot (build spec)

The executable reference for this dialog is `src/wilmington_pay/ivr.py`. The prompts and branching below match it, and the local simulator plays it. Build these blocks in the Connect flow designer and export the JSON into `infra/connect/` once it is built in the City's account.

## Instance prerequisites

1. Claim a DID for the payment line. It becomes `PaymentIvrNumber`.
2. **Encryption:** generate an RSA key pair. Upload the public certificate under *Instance → Flows → Encryption* (the Connect UI shows a **key id**, which becomes `ConnectCertificateId`). Store the PEM private key in Secrets Manager (`ConnectPrivateKeySecretArn`).
3. Turn **off** call recording, Contact Lens and transcripts for this flow.
4. Add the `ConnectPaymentFunction` Lambda to the instance. The SAM template does this through `AWS::Connect::IntegrationAssociation`.
5. Create a Lex V2 bot `CowPaymentAssistant` (spec below) and add it to the instance.

## Flow: `CoW-311-SecurePayment`

| # | Block | Configuration | Branches |
|---|---|---|---|
| 1 | Set logging behavior | **Disabled** | → 2 |
| 2 | Set recording behavior | Agent: off, Customer: off | → 3 |
| 3 | Set voice | Polly *Joanna* (neural) | → 4 |
| 4 | Play prompt | "Welcome to the City of Wilmington secure payment line." | → 5 |
| 5 | Invoke Lambda | `action=lookup` | → 6 |
| 6 | Check contact attributes | `$.External.found = true` | true → 9, false → 7 |
| 7 | Store customer input | "Please enter the six digit payment code your representative gave you, followed by the pound key." Max 6 digits, `#` terminator, **no encryption** (not card data) | → 8 |
| 8 | Invoke Lambda | `action=lookup`, `paymentCode=$.StoredCustomerInput` | found → 9; else loop to 7 (max 3), then → 20 |
| 9 | Set contact attributes | `sessionId`, `amountSpoken` from `$.External` | → 10 |
| 10 | Get customer input (**Lex**) | Bot `CowPaymentAssistant`. Prompt: "You are paying $.Attributes.amountSpoken for special trash pickup. Say yes or press 1 to continue, or say agent." | `Confirm` → 11, `ReturnToAgent` → 19, `Repeat` → 10 |
| 11 | Store customer input | "Enter your card number followed by the pound key…" **Encrypt entry = ON**, key id = cert. Max 19, `#` terminator | → 12 |
| 12 | Set contact attributes | `encCardNumber = $.StoredCustomerInput` | → 13 |
| 13 | Store customer input | "Enter the four digit expiration date…" **Encrypt ON**, max 4 | → `encExpiry` |
| 14 | Store customer input | "Enter the security code…" **Encrypt ON**, max 4, `#` terminator | → `encCvv` |
| 15 | Store customer input | "Enter the five digit billing ZIP code." No encryption, max 5 | → `postalCode` |
| 16 | Invoke Lambda | `action=charge`, `sessionId`, `encCardNumber`, `encExpiry`, `encCvv`, `postalCode`. Timeout 8 s | → 17 |
| 17 | Check contact attributes | `$.External.result` | `APPROVED` → 18; `RETRY` / `INVALID_CARD` → play `$.External.message` → 11 (max 3); `DECLINED` / `FAILED` / `ERROR` → 20 |
| 18 | Play prompt | "Thank you. Your payment was approved. Your confirmation number is $.External.authCode." | → 21 |
| 19 | Invoke Lambda | `action=cancel`, `reason=caller_requested_agent` | → 21 |
| 20 | Play prompt | "We were not able to complete your payment. Transferring you back to the representative." | → 21 |
| 21 | Transfer to phone number | `AgentReturnNumber` (311 RingCentral queue, or the originating agent's DID; see gap item 3) | Error → Disconnect |

Also add a **Set disconnect flow** block that calls `action=cancel` if the caller hangs up mid-payment.

## Lex V2 bot: `CowPaymentAssistant` (en_US)

Lex is used **only** for non-sensitive conversational turns. It must have **no card slots**.

| Intent | Sample utterances | DTMF |
|---|---|---|
| `Confirm` | yes, yeah, that's right, correct, go ahead, continue | `1` |
| `ReturnToAgent` | no, agent, representative, talk to a person, that's wrong | `2`, `*` |
| `Repeat` | repeat, say that again, what was the amount | `9` |
| `FallbackIntent` | (built-in) | re-prompt, then route to `ReturnToAgent` after 2 misses |

Conversation logs: text logs may stay enabled for this bot because it never handles card data. Audio logs must be **off**.
