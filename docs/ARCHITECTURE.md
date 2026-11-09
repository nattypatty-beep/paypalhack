# Architecture

MandatePay is a small, single-process Python application with a deliberately
boring deployment story: **FastAPI + SQLite + static files**. There is no Node
build, no message broker, no cloud dependency and no background worker. That is
a feature for a hackathon demo: a judge can clone the repo, run one command, and
see the entire flow with no credentials.

The architecture exists to enforce one invariant:

> An AI agent may spend money only inside a human mandate, and money is released
> only after the delivered work passes verification.

Everything else — planning, Payments, Payouts, AG Grid, the audit chain — serves
that sentence.

---

## 1. System context

```text
┌──────────────────────────── Browser ─────────────────────────────┐
│  Vanilla JS single-page UI                                       │
│  ├─ mandate composer + plan preview                              │
│  ├─ jobs / milestones / approval inbox                           │
│  ├─ verification studio + evidence                               │
│  ├─ AG Grid audit log + payments trail                           │
│  └─ demo director (8 scripted stages)                            │
└───────────────┬──────────────────────────────────────────────────┘
                │ JSON over HTTP + Server-Sent Events
┌───────────────▼──────────────────────────────────────────────────┐
│  FastAPI (app/main.py)                                           │
│  ├─ REST API + OpenAPI docs at /docs                             │
│  ├─ SSE /api/stream for state pushes                             │
│  ├─ static UI, sample deliverables, uploads                      │
│  └─ exception handlers -> stable JSON errors                     │
└───────────────┬──────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────┐
│  AppService (app/service.py) — the orchestration layer           │
│  ├─ command: parse mandate -> draft plan -> create job           │
│  ├─ authorize: mandate engine -> PayPal hold                     │
│  ├─ submit: inspect files -> store submission                    │
│  ├─ verify: rules + optional LLM -> settle if accepted           │
│  └─ release: partial capture -> Payout -> audit                  │
└───────┬─────────────────┬──────────────────┬─────────────────────┘
        │                 │                  │
┌───────▼──────┐ ┌────────▼────────┐ ┌───────▼────────────┐
│ MandateEngine│ │ Planner/Verifier│ │ PayPalClient       │
│ app/mandate  │ │ app/planner     │ │ app/paypal         │
│              │ │ app/verify      │ │ real or mock       │
└──────────────┘ └────────┬────────┘ └───────┬────────────┘
                          │                  │
                   ┌──────▼──────┐    ┌──────▼────────────┐
                   │ inspect.py  │    │ Orders v2         │
                   │ file facts  │    │ Authorizations    │
                   └─────────────┘    │ Payouts           │
                                      └───────────────────┘
        ┌────────────────────────────────────────────────────┐
        │ Store (app/store.py) — SQLite + hash-chained audit │
        └────────────────────────────────────────────────────┘
```

### Design principles

1. **The server is the source of truth.** The browser only renders state and
   invokes endpoints. It cannot approve a spend by changing client-side state.
2. **Money is integer cents.** `app/money.py` is the only place formatting and
   allocation live; no float arithmetic touches a balance.
3. **Hard rules are not negotiable.** Human approval unlocks an above-threshold
   spend. It never overrides budget, category, expiry or per-transaction limits.
4. **Authorize, then verify, then capture.** A PayPal authorization is a hold,
   not a payment. Capture happens per accepted milestone.
5. **Evidence over vibes.** Verification is rules-first and records per-criterion
   evidence. An LLM can add judgment, not erase a failed rule.
6. **Honest fallbacks.** With no credentials the UI says *PayPal mock* and
   *Rules engine*. It never pretends an LLM reviewed the work.
7. **Every decision leaves a receipt.** Append-only audit rows are SHA-256
   hash-chained, and the chain can be verified or deliberately tampered with.

---

## 2. Request lifecycle

### 2.1 Human instruction → mandate → plan

`POST /api/command` with:

```text
Get me a logo for my bakery, max $60, ask me before anything over $40.
```

1. `app/nlp.py` parses budget, approval threshold, category, expiry and limits.
   It is deterministic and regex/rule based. If an LLM is configured, it can be
   used as a fallback or refinement, but the deterministic result is the
   baseline.
2. A mandate row is written with `budget_cents=6000`,
   `approval_threshold_cents=4000`, category `design`, and a default 7-day
   expiry.
3. `app/planner.py` drafts a milestone plan. The deterministic logo template
   allocates the budget as `[1500, 2500, 2000]` cents across concept, master and
   handoff. Each milestone carries machine-checkable criteria.
4. The plan is validated: every amount is positive, every milestone has at least
   one criterion, and the milestone total must equal the job amount. A bad LLM
   plan is repaired or replaced by the template.
5. With `post=true` the mandate and plan become a job. With `post=false` the
   endpoint is a dry-run preview used by the UI.
6. Each step writes an audit row (`mandate.created`, `plan.drafted`,
   `job.created`).

### 2.2 Authorization

`POST /api/jobs/{id}/authorize`:

1. The mandate engine evaluates an `authorization` spend.
2. If the verdict is `block`, the job becomes `blocked` and the audit row records
   the reason codes. No payment object is created.
3. If the verdict is `require_approval`, an approval row is created and the job
   becomes `awaiting_approval`. No payment object is created.
4. If the verdict is `allow`, PayPal creates an order with `intent=AUTHORIZE` and
   authorizes it. The job stores the order and authorization IDs and moves to
   `authorized`. Funds are held, not captured.
5. A human resolution of an approval sets the approval to `approved` and issues a
   short-lived token. Re-authorizing with that approval consumes it exactly once.

The endpoint is intentionally idempotent for retries:

- an already authorized/in-progress/completed job returns
  `already_authorized`;
- a job already awaiting approval returns its existing pending approval instead
  of double-counting the reserved amount against the budget.

### 2.3 Submission and verification

`POST /api/milestones/{id}/submissions`:

1. Deliverables come either from a named sample (demo packages) or from uploaded
   files. Samples are limited to plain filenames; uploads are extension checked,
   size checked and stored under `data/uploads/`.
2. `app/inspect.py` reads the actual bytes and records facts: extension, MIME,
   size, SHA-256, dimensions, SVG colour palette, full-bleed background flag,
   extracted text, word count, script tags, validity.
3. `POST /api/submissions/{id}/verify` runs the verifier:
   - objective criteria against inspection facts;
   - optional LLM for qualitative criteria;
   - weighted score and a verdict of `pass`, `revise` or `escalate`.
4. `pass` immediately releases the milestone: partial capture, optional Payout,
   audit rows. `revise` returns evidence and leaves the money held. `escalate`
   waits for a human decision.

### 2.4 Release and settlement

`release_milestone` re-runs the mandate engine in `capture` mode:

- the job must have an active authorization;
- the capture cannot exceed the remaining authorized amount;
- category and budget are still enforced;
- the approval threshold is **not** re-triggered, because this is drawing down a
  hold the human already authorized.

Then:

1. PayPal captures the milestone amount (`final_capture=true` on the last one).
2. A payment row of kind `capture` is written.
3. If Payouts are enabled, the freelancer receives the net amount. The optional
   platform fee is `amount * PLATFORM_FEE_BPS / 10000`.
4. A payment row of kind `payout` is written.
5. The milestone becomes `released`; the job becomes `completed` when all
   milestones are released.
6. Audit rows record `payment.released` and `payout.completed` (or explicit
   failure/skip rows).

---

## 3. Mandate engine

Source: [`app/mandate.py`](../app/mandate.py).

### 3.1 Inputs

A `SpendRequest` has mandate, amount, currency, category, kind
(`authorization`, `capture`, `simulation`), job/milestone IDs, description and an
optional approval ID.

### 3.2 Check order

The engine accumulates **reasons** rather than failing fast, so a client sees
every problem at once.

| Order | Check | Kind | Overridable by approval? |
| --- | --- | --- | --- |
| 1 | mandate exists and is `active` | hard | no |
| 2 | mandate has not expired | hard | no |
| 3 | amount > 0 | hard | no |
| 4 | currency matches | hard | no |
| 5 | category is allowed (or `any`) | hard | no |
| 6 | per-transaction limit | hard | no |
| 7 | daily authorization limit | hard | no |
| 8 | projected budget (`spent + committed + pending + request`) | hard | no |
| 9 | `amount > approval_threshold` | approval | yes, with a valid approval |
| 10 | approval binding (mandate/job/amount/category, unused, unexpired) | hard | — |

For `capture`, the budget check is different: it verifies the mandate is not
already over budget and that the capture fits the remaining authorization. It
does not add the capture to projected spend again, because the authorization
already reserved it.

### 3.3 Approval as a capability

An approval is not a boolean. It is a row bound to:

- one mandate;
- one job;
- one exact amount in cents;
- one category;
- a request expiry (`APPROVAL_TTL_MINUTES`, default 30);
- a token expiry after human approval (`APPROVAL_TOKEN_TTL_MINUTES`, default 20);
- a single `used_at` consumption timestamp.

Consequences:

- the same approval cannot authorize a different job or a higher amount;
- the same approval cannot be replayed after use;
- an approval cannot rescue a hard-blocked spend;
- a pending approval counts as `pending_cents` in the budget calculation, so two
  simultaneous approvals cannot both consume the last dollar;
- duplicate authorization attempts return the existing pending approval.

### 3.4 Financial definitions

`Store.mandate_financials()` computes:

| Field | Meaning |
| --- | --- |
| `spent_cents` | captured payments (real money released) |
| `committed_cents` | authorized but not yet captured |
| `pending_cents` | unexpired pending approvals |
| `available_cents` | `budget - spent - committed - pending` (floored at 0) |
| `authorized_cents` | total active authorizations |
| `payouts_cents` | completed Payouts |

A hard budget block occurs when
`spent + committed + pending + request > budget`.

---

## 4. Verification pipeline

Sources: [`app/inspect.py`](../app/inspect.py),
[`app/verify.py`](../app/verify.py).

### 4.1 Objective facts

The inspector handles:

- **SVG:** parses XML safely (rejects DTD/entity declarations), reads width,
  height and `viewBox`, extracts fill/stroke/style colours, detects full-bleed
  background rectangles, collects text, flags `<script>`.
- **PNG/JPEG/GIF/WebP:** reads dimensions from headers.
- **PDF:** reads the `MediaBox` dimensions.
- **Text/Markdown/JSON/CSV:** reads text, counts words and characters.
- **Audio/video/archives:** records format and size facts.

Every file also gets a SHA-256 content hash.

### 4.2 Criterion types

`file_format`, `min_dimensions`, `vector_source`, `color_palette_max`,
`transparent_background`, `placeholder_free`, `keyword_coverage`,
`concepts_count`, `notes_min_words`, `text_min_words`, `license_included`,
`file_count`, `text_contains`, `max_file_size_mb`, `qualitative`,
`human_review`.

Each result carries `passed`, `skipped`, `weight`, `evidence`, `detail` and
`source` (`rules`, `ai` or `human`).

### 4.3 Verdict logic

```text
any failed criterion          -> revise
else any required human review -> escalate
else                           -> pass
```

The score is weighted: `passed_weight / evaluated_weight`. Skipped criteria are
excluded from the denominator, except that a skipped `human_review` forces
`escalate`.

### 4.4 LLM guardrail

When a model is configured, it reviews qualitative/`ai_assisted` criteria and
returns structured JSON. Merge rules:

- for purely qualitative criteria, a confident model judgment can set pass/fail;
- for objective criteria, the model may *flag* a disagreement but can never turn
  a rule failure into a pass;
- confidence below `LLM_MIN_CONFIDENCE` is ignored;
- prompts explicitly treat submission text as untrusted data, not instructions;
- if no model is configured, the qualitative review is marked skipped and the
  UI says so.

---

## 5. Payments adapter

Source: [`app/paypal.py`](../app/paypal.py). Full sandbox details are in
[PAYPAL.md](PAYPAL.md).

`PayPalClient` exposes:

| Method | Real PayPal | Mock |
| --- | --- | --- |
| `create_order` | `POST /v2/checkout/orders` (`intent=AUTHORIZE`) | row in `paypal_objects` |
| `authorize_order` | `POST /v2/checkout/orders/{id}/authorize` | deterministic state transition |
| `capture_authorization` | `POST /v2/payments/authorizations/{id}/capture` | remaining-amount check, partial capture |
| `void_authorization` | `POST /v2/payments/authorizations/{id}/void` | `VOIDED` |
| `create_payout` | `POST /v1/payments/payouts` | deterministic success |
| `verify_webhook` | `POST /v1/notifications/verify-webhook-signature` | always true |

Mode selection:

```text
PAYPAL_FORCE_MOCK=true          -> mock
no client id/secret             -> mock
credentials + PAYPAL_ENV=live   -> live only if ALLOW_LIVE=1, else sandbox
credentials + PAYPAL_ENV=sandbox-> sandbox
```

Every real request uses `PayPal-Request-Id` for idempotency, `Prefer:
return=representation`, and a cached OAuth token refreshed on expiry/401. The
mock implements the same call signatures and state machine so the UI and tests
do not branch on mode.

---

## 6. Data model

Source: [`app/store.py`](../app/store.py). The database is one SQLite file at
`data/mandatepay.sqlite3`. JSON is stored as text and decoded in the store
layer; no ORM.

| Table | Purpose |
| --- | --- |
| `mandates` | The human policy: budget, threshold, limits, categories, expiry |
| `jobs` | A scoped engagement, PayPal order/auth IDs, status |
| `milestones` | Plan slices, amount, criteria JSON, submission/capture/payout links |
| `submissions` | Deliverables JSON, notes, verdict |
| `approvals` | Threshold requests, binding fields, token, single-use marker |
| `payments` | Authorizations, captures, Payouts, receipts, statuses |
| `audit` | Append-only hash-chained decisions |
| `paypal_objects` | Mock PayPal state machine (orders/auths/captures/payouts) |
| `uploads` | Uploaded file metadata and storage path |

Money columns are `INTEGER` cents. Timestamps are UTC ISO-8601 strings with `Z`.

### 6.1 Audit chain

Each row stores `prev_hash` and `hash`:

```text
body = {
  seq, id, ts, actor, action, subject_type, subject_id, decision,
  amount_cents, currency, reason_codes, message, meta, prev_hash
}
hash = sha256(canonical_json(body))
```

- the first row's `prev_hash` is 64 zeroes (genesis);
- `GET /api/audit/verify` recomputes every link and reports the first mismatch;
- `POST /api/audit/tamper` deliberately edits the newest message so the next
  verification fails at that sequence number (demo mode only);
- inserting a row and reading the previous hash happen inside one SQLite
  transaction, so concurrent writers cannot fork the chain in a single process.

The chain proves internal consistency, not external notarization. See
[SECURITY.md](../SECURITY.md).

---

## 7. Frontend

Source: [`app/static/app.js`](../app/static/app.js),
[`index.html`](../app/static/index.html), [`styles.css`](../app/static/styles.css).

- **No framework, no bundler.** A single IIFE renders panels from the server
  state. `state.data` is replaced by `GET /api/state`; actions call the API and
  then refresh.
- **Live updates.** `GET /api/stream` is a Server-Sent Events endpoint. The
  server publishes state versions on every mutation; the client refreshes when
  the version changes. Polling is a fallback.
- **Verification studio.** Users can pick a feasible milestone, choose a demo
  package or upload files, submit and verify, then read the per-criterion
  evidence.
- **Approval modal.** Shows the exact amount, job, category, threshold and
  expiry. Approving authorizes the hold; it does not release money.
- **AG Grid Community v36.3.0** is vendored under `app/static/vendor/`, uses the
  Theming API (no legacy CSS), and supports quick filter, decision filter,
  sorting and an audit/payments tab switch. If grid initialization fails, the UI
  renders a plain HTML table with the same data.
- **Demo director.** Eight stages drive the public API: mandate, budget probe,
  job + approval, concept pass, draft revise, final pass, brand-kit pass, audit
  verification. It is a UI convenience, not a hidden back door; every stage uses
  the same endpoints a judge can call with `curl`.

---

## 8. AI integration

`app/llm.py` supports OpenAI-compatible chat completions, Anthropic messages,
and Ollama's OpenAI-compatible endpoint. It is optional by design.

| Path | No model | Model configured |
| --- | --- | --- |
| Mandate parsing | deterministic rules | rules baseline + optional LLM refinement |
| Planning | category templates | LLM JSON plan, validated; fallback to template |
| Verification | objective rules, qualitative skipped | rules + qualitative judgment, hard-rule guardrail |
| UI label | `Rules engine` | model name |

LLM failure is non-fatal: timeouts, malformed JSON or low confidence fall back to
the deterministic path and are logged as such. The application never blocks a
payment on an LLM response; verification rules always run.

---

## 9. Extension points

- **More categories:** add a template in `app/planner.py` and criteria in
  `app/models.py` if needed.
- **More inspections:** add a parser in `app/inspect.py`; return the same fact
  keys so criteria compose.
- **More payment rails:** implement the `PayPalClient` method surface (or wrap it)
  and keep the service-level state machine unchanged.
- **External audit anchoring:** periodically publish the chain head to an
  append-only store and record the anchor as another audit row.
- **Multi-tenant:** add `tenant_id` to mandates/jobs/payments/audit, enforce it in
  every store query, and isolate uploads per tenant.
- **Webhook reconciliation:** the receiver already verifies signatures and marks
  matching payments failed on denial/refund/failed-payout events.

---

## 10. Testing strategy

The suite is 45 tests and runs entirely in mock mode with a temporary data
directory.

| Area | What is asserted |
| --- | --- |
| Mandate engine | strict threshold, hard blocks, category, per-txn, daily, expiry |
| Approvals | single use, amount/job binding, rejection, expiry |
| Captures | remaining authorization, no threshold re-trigger, budget |
| Planner | budget-sum invariant, criteria validity, category selection |
| API flow | block → approve → authorize → reject draft → release → payout |
| Audit | link verification, tamper detection, genesis |
| Inspection | objective SVG facts, invalid file handling |

Run:

```bash
python -m pytest -q
python -m compileall -q app tests scripts
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

The smoke script exercises a **running** server, resets the demo before and
after, and asserts the hard budget block and chain health.

---

## 11. Deliberate non-goals

- No real user accounts or authentication; this is a single-principal demo.
- No multi-currency conversion; a mandate has one currency.
- No dispute/chargeback workflow.
- No distributed transaction between capture and Payout. The app records the
  capture, then attempts the payout, and records a failure explicitly if the
  payout fails. A production system needs a reconciliation worker.
- No malware scanning for uploads, no object storage, no per-tenant isolation.
- No claim that an authorization is escrow in the legal sense.

These are documented so the demo is judged on what it actually does.
