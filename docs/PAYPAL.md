# PayPal integration

MandatePay uses four PayPal REST capabilities:

1. **Orders v2** to create an order with `intent=AUTHORIZE`.
2. **Authorizations** to hold the job total without capturing it.
3. **Captures** to release one milestone at a time after verification.
4. **Payouts** to pay the freelancer after each capture.

It also verifies **webhook signatures** and records the resulting events.

The code lives in [`app/paypal.py`](../app/paypal.py). The default configuration
runs a deterministic mock adapter, so the demo needs no credentials. This
document explains how to use the real sandbox safely.

> **Safety default:** live PayPal is blocked unless **both** real credentials and
> `ALLOW_LIVE=1` are present. Do not set that flag for the hackathon demo.

---

## 1. Sandbox setup

### 1.1 Create a sandbox app

1. Sign in at <https://developer.paypal.com/dashboard/>.
2. Open **Apps & Credentials** and switch to **Sandbox**.
3. Click **Create App**, name it `MandatePay`, and choose a sandbox business
   account as the owner.
4. Copy the **Client ID** and **Secret**.

Put them in `.env`:

```dotenv
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_ENV=sandbox
ALLOW_LIVE=false
```

Restart the server. The header should change from **PayPal mock** to
**PayPal sandbox**. If it does not, check `PAYPAL_FORCE_MOCK` and the server log
line:

```text
MandatePay ready: paypal=sandbox llm=rules demo=True
```

### 1.2 Sandbox accounts and money

The app owner is the **payer** (the business funding the mandate). The freelancer
is the **receiver** of the Payout.

- In **Sandbox → Accounts**, confirm the business account has a balance.
  Sandbox accounts usually start funded; if Payouts fail with an insufficient
  funds error, use **Add funds** in the sandbox account view.
- Create or use a sandbox personal/business account for the freelancer and set
  its email:

  ```dotenv
  FREELANCER_NAME=Avery Quinn
  FREELANCER_EMAIL=avery@example.com
  ```

  The address must belong to a sandbox account that can receive Payouts. Using a
  random real address will make the Payouts call fail.

### 1.3 Payouts permission

Payouts are a separate product. If the API returns an authorization/permission
error:

- confirm the app owner account is enabled for Payouts in the sandbox;
- confirm `PAYPAL_PAYOUTS_ENABLED=true`;
- if Payouts are unavailable, set `PAYPAL_PAYOUTS_ENABLED=false`. Captures still
  work and the audit log records `payout.skipped` honestly.

### 1.4 Webhooks (optional)

Webhooks are not required for the demo. To exercise them:

1. Expose your local server over HTTPS (for example with `cloudflared tunnel` or
   `ngrok`).
2. In the sandbox app, **Webhooks → Add Webhook**:
   - URL: `https://<your-tunnel>/api/webhooks/paypal`
   - Events: at minimum
     - `PAYMENT.AUTHORIZATION.CREATED`
     - `PAYMENT.CAPTURE.COMPLETED`
     - `PAYMENT.CAPTURE.DENIED`
     - `PAYMENT.CAPTURE.REFUNDED`
     - `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`
     - `PAYMENT.PAYOUTS-ITEM.FAILED`
3. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`.

The receiver calls PayPal's verification endpoint with the transmission headers
and the raw event. Unverified events are still audited, but marked
`unverified` and cannot mutate payment state.

---

## 2. Configuration reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAYPAL_CLIENT_ID` | empty | Sandbox/live app client ID |
| `PAYPAL_CLIENT_SECRET` | empty | App secret |
| `PAYPAL_ENV` | `sandbox` | `sandbox` or `live` |
| `PAYPAL_WEBHOOK_ID` | empty | Enables signature verification |
| `PAYPAL_PAYOUTS_ENABLED` | `true` | Send Payouts after capture |
| `PAYPAL_FORCE_MOCK` | `false` | Ignore credentials and use the mock |
| `ALLOW_LIVE` | `false` | Explicit live-money opt-in |
| `FREELANCER_EMAIL` | `freelancer@example.com` | Payout receiver |
| `PLATFORM_FEE_BPS` | `0` | Basis points withheld from the freelancer |

Mode resolution:

```python
if PAYPAL_FORCE_MOCK:                 mock
elif not client_id or not secret:     mock
elif PAYPAL_ENV == "live" and not ALLOW_LIVE: sandbox
elif PAYPAL_ENV == "live":            live
else:                                 sandbox
```

This is implemented as a property in [`app/config.py`](../app/config.py). The UI
reads the resolved mode from `/api/state` and never guesses.

---

## 3. API calls

### 3.1 Get an access token

```http
POST /v1/oauth2/token
Authorization: Basic base64(client_id:secret)
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

The client caches the token until 60 seconds before expiry and retries once on a
401. Tokens are never written to disk or the audit log.

### 3.2 Create an order (hold intent)

```http
POST /v2/checkout/orders
Authorization: Bearer <token>
PayPal-Request-Id: order-<job_id>
Prefer: return=representation
Content-Type: application/json

{
  "intent": "AUTHORIZE",
  "purchase_units": [{
    "custom_id": "<job_id>",
    "description": "MandatePay escrow: Logo & brand mark",
    "amount": { "currency_code": "USD", "value": "60.00" }
  }],
  "application_context": {
    "brand_name": "MandatePay",
    "shipping_preference": "NO_SHIPPING",
    "user_action": "PAY_NOW"
  }
}
```

`value` is always formatted from integer cents by `app/money.py`; the service
never passes a float.

### 3.3 Authorize the order

```http
POST /v2/checkout/orders/{order_id}/authorize
PayPal-Request-Id: auth-<job_id>
Prefer: return=representation
Content-Type: application/json

{}
```

The authorization ID is read from:

```text
purchase_units[0].payments.authorizations[0].id
```

At this point funds are **held**, not captured. The app writes an
`authorization` payment row and sets the job to `authorized`.

### 3.4 Capture one milestone

```http
POST /v2/payments/authorizations/{authorization_id}/capture
PayPal-Request-Id: capture-<milestone_id>
Prefer: return=representation
Content-Type: application/json

{
  "amount": { "currency_code": "USD", "value": "15.00" },
  "final_capture": false
}
```

The last milestone uses `"final_capture": true`. The service sets that flag when
the milestone amount equals the remaining authorized amount.

> **Merchant configuration matters.** PayPal supports multiple partial captures
> on an authorization for eligible merchants/currencies. If your sandbox account
> rejects a partial capture, you can still demo the flow by using one milestone
> per job, or by authorizing each milestone separately. The app records the
> PayPal error and leaves the milestone in `awaiting_review` rather than claiming
> success.

### 3.5 Void the hold

```http
POST /v2/payments/authorizations/{authorization_id}/void
PayPal-Request-Id: void-<job_id>
Prefer: return=representation
Content-Type: application/json

{}
```

Cancelling a job voids the remaining authorization. Already captured milestones
are **not** refunded automatically; a production system would add a refund flow.

### 3.6 Pay the freelancer

```http
POST /v1/payments/payouts
PayPal-Request-Id: payout-<milestone_id>
Prefer: return=representation
Content-Type: application/json

{
  "sender_batch_header": {
    "sender_batch_id": "payout-<milestone_id>",
    "email_subject": "You have a payment from MandatePay",
    "email_message": "Payment for Logo & brand mark — Concept directions"
  },
  "items": [{
    "recipient_type": "EMAIL",
    "amount": { "currency_code": "USD", "value": "15.00" },
    "receiver": "avery@example.com",
    "note": "Payment for Logo & brand mark — Concept directions",
    "sender_item_id": "<milestone_id>"
  }]
}
```

- `sender_batch_id` and `sender_item_id` are stable per milestone, so retries do
  not create duplicate payouts.
- The amount is the capture minus `platform_fee_bps`.
- The response `batch_header.batch_status` is stored in the payment row. Sandbox
  batches usually settle immediately; the UI treats `SUCCESS` as completed and
  displays other statuses as pending/failed.

### 3.7 Verify a webhook

```http
POST /v1/notifications/verify-webhook-signature
Authorization: Bearer <token>
Content-Type: application/json

{
  "transmission_id": "...",
  "transmission_time": "...",
  "cert_url": "...",
  "auth_algo": "...",
  "transmission_sig": "...",
  "webhook_id": "<PAYPAL_WEBHOOK_ID>",
  "webhook_event": { ... }
}
```

A successful response has `verification_status == "SUCCESS"`.

Handled events:

- `PAYMENT.CAPTURE.DENIED`
- `PAYMENT.CAPTURE.REFUNDED`
- `PAYMENT.PAYOUTS-ITEM.FAILED`

These mark the matching payment `failed`. Every event is audited with the
verified flag; unverified events do not change payment state.

---

## 4. Idempotency and retries

| Operation | Key |
| --- | --- |
| Create order | `order-<job_id>` |
| Authorize | `auth-<job_id>` |
| Capture | `capture-<milestone_id>` |
| Void | `void-<job_id>` |
| Payout | `payout-<milestone_id>` |

The service also has local idempotency:

- authorizing an already authorized job returns `already_authorized`;
- authorizing an `awaiting_approval` job returns the existing pending approval;
- releasing an already released milestone returns `already_released`;
- mock PayPal enforces the same state transitions as the real API.

If the process crashes between capture and payout, the payment rows show the
capture and a missing/failed payout. That is deliberate: the audit trail should
show the gap rather than hide it. A production deployment needs a reconciliation
job.

---

## 5. Error handling

`PayPalError` carries the HTTP status and response details. The service:

- sets the job to `payment_error` if authorization fails;
- keeps the milestone in `awaiting_review` if capture fails;
- records a failed payout payment row if the capture succeeded but the payout
  did not;
- writes `paypal.error` / `payout.failed` audit rows with the PayPal message and
  details.

The UI shows a human-readable error and never retries a real payment silently.

---

## 6. Live-money guardrails

The app refuses to talk to live PayPal unless:

```dotenv
PAYPAL_ENV=live
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
ALLOW_LIVE=1
```

Even then:

- every authorization must pass the mandate engine;
- approvals are single-use and expire;
- captures are limited to the remaining authorization;
- Payouts require an explicit receiver email;
- the audit log records every external call and its identifiers.

Before using live credentials, consider the non-goals in
[ARCHITECTURE.md](ARCHITECTURE.md#11-deliberate-non-goals). This prototype is
not a licensed escrow service and has no chargeback or dispute workflow.

---

## 7. Mock adapter

The mock is not a stub that returns `true`. It persists state in
`paypal_objects` and enforces:

- an order can be authorized once;
- an authorization can only be captured while `AUTHORIZED` or
  `PARTIALLY_CAPTURED`;
- a capture cannot exceed the remaining authorized amount;
- partial captures update `captured_cents` and the authorization status;
- a void sets `VOIDED`;
- a Payout succeeds deterministically.

That is why the demo can show a rejected draft, a released milestone and a
payout without any network access. The UI labels it **PayPal mock** in the header
and on payment rows.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Header says `PayPal mock` with credentials set | `PAYPAL_FORCE_MOCK=true` or secret missing | unset the flag, recheck `.env`, restart |
| `Could not obtain a PayPal access token` | wrong client ID/secret or wrong environment | regenerate sandbox credentials |
| `PayPal API error: ... PERMISSION_DENIED` on Payouts | Payouts not enabled for the app owner | enable Payouts or set `PAYPAL_PAYOUTS_ENABLED=false` |
| Payout `INSUFFICIENT_FUNDS` | sandbox payer balance too low | add funds to the sandbox business account |
| Capture fails after 29 days | authorization expired | re-authorize the job |
| Partial capture rejected | merchant/currency does not support it | use one milestone per job or separate authorizations |
| Webhook always unverified | missing/wrong `PAYPAL_WEBHOOK_ID` or header forwarding | check the webhook ID and tunnel preserves headers |
| `ALLOW_LIVE` ignored | `PAYPAL_ENV` is not `live` | set both, intentionally |

---

## 9. Reference links

- Orders v2: <https://developer.paypal.com/docs/api/orders/v2/>
- Payments v2 (authorizations/captures): <https://developer.paypal.com/docs/api/payments/v2/>
- Payouts: <https://developer.paypal.com/docs/api/payments.payouts-batch/v1/>
- Webhook verification: <https://developer.paypal.com/api/rest/webhooks/rest/>
- Sandbox dashboard: <https://developer.paypal.com/dashboard/>
