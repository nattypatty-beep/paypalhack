# MandatePay

**Agentic commerce with a leash.**

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![License MIT](https://img.shields.io/badge/license-MIT-7af0bd)
![PayPal Orders v2](https://img.shields.io/badge/PayPal-Orders%20v2%20%2B%20Payouts-003087)
![AG Grid Community](https://img.shields.io/badge/AG%20Grid-Community%20v36-1f6feb)

MandatePay is an AI procurement agent that can hire freelancers, but only inside a
human-set **mandate**. The agent parses a plain-English instruction into a budget,
category, per-transaction and daily limits, and an approval threshold. It drafts a
milestone plan, authorizes a PayPal payment, verifies delivered work against
machine-checkable criteria, and releases money milestone by milestone.

The point is the **leash**: an AI agent should be able to move money without being
trusted with a blank cheque.

<img src="docs/screenshot.png" alt="MandatePay dashboard after a completed demo: mandate, jobs, verification studio, approval inbox and hash-chained audit log" width="960">

> Runs offline out of the box with a deterministic mock PayPal adapter and a rules
> planner/verifier. Add sandbox credentials to switch to real PayPal Orders,
> authorizations, partial captures and Payouts. Live money requires an explicit
> `ALLOW_LIVE=1` opt-in.

---

## The 60-second story

1. **A human gives the agent a mandate.**
   `Get me a logo for my bakery, max $60, ask me before anything over $40.`
2. **The agent tries to overspend.**
   A $75 rush job is **hard-blocked**. No approval can override the budget cap.
3. **The agent revises to $60.**
   That is over the $40 threshold, so the job pauses in the **human approval inbox**.
4. **The human approves the hold.**
   PayPal authorizes the funds. Nothing is captured yet.
5. **The verifier checks the work.**
   The concept board passes, a placeholder draft is rejected with evidence, the final
   vector passes, and the brand kit passes.
6. **Money moves only after verification.**
   Each accepted milestone is a partial capture; the freelancer is paid via PayPal
   Payouts. Every decision is appended to a SHA-256 hash chain shown in AG Grid.

Run the whole scripted story from the **Demo director** at the bottom of the page.

---

## Why this matters

Agentic commerce has an awkward gap: giving an autonomous agent a checkout button is
either useless (a human approves every cent) or reckless (the agent has your card).
MandatePay proposes a middle layer:

- **Machine-enforced limits** that an approval cannot override.
- **Human approval as a capability**, not a blanket permission: single-use, bound to
  one job, amount and category, with a short expiry.
- **Escrow-style settlement**: authorize up front, capture only after objective
  verification.
- **A tamper-evident receipt** for every action, because "the agent did it" is not an
  audit trail.

---

## What is in the box

| Area | Implementation |
| --- | --- |
| Agent instruction parsing | Deterministic rules parser, optional LLM fallback |
| Milestone planning | Deterministic templates, optional LLM JSON plan with budget repair |
| Mandate engine | Budget, category, expiry, per-transaction, daily, threshold, approval binding |
| Payments | PayPal Orders v2 authorize, partial capture, void; PayPal Payouts |
| Work verification | File inspection (formats, dimensions, colours, transparency, placeholders, text) + optional LLM judgment |
| Audit log | Append-only SQLite with SHA-256 hash chain and a tamper-test button |
| UI | Vanilla JS + CSS, no build step; AG Grid Community v36 vendored locally |
| Tests | 45 pytest tests covering rules, planner, verifier, API flow and tamper detection |

---

## Quickstart

**Requirements:** Python 3.11+ (tested on 3.13). No Node, no database server, no
build step.

### Windows (PowerShell)

```powershell
git clone https://github.com/<your-account>/mandatepay.git
cd mandatepay
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

### macOS / Linux

```bash
git clone https://github.com/<your-account>/mandatepay.git
cd mandatepay
chmod +x start.sh
./start.sh
```

### Manual

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The app starts in mock mode and creates
`data/mandatepay.sqlite3` on first launch.

### Configuration

Copy `.env.example` to `.env` only when you want to change a default. The app loads
`.env` automatically without a third-party dotenv dependency.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | empty | Sandbox app credentials; empty means mock |
| `PAYPAL_ENV` | `sandbox` | `sandbox` or `live` |
| `PAYPAL_PAYOUTS_ENABLED` | `true` | Send freelancer payouts after capture |
| `PAYPAL_FORCE_MOCK` | `false` | Keep the mock even with credentials |
| `ALLOW_LIVE` | `false` | Required before live PayPal is used |
| `FREELANCER_EMAIL` | `freelancer@example.com` | Payout receiver |
| `LLM_PROVIDER` | `auto` | `auto`, `openai`, `anthropic`, `ollama`, `none` |
| `LLM_API_KEY` / `LLM_MODEL` | empty | Enable the optional AI planner/verifier |
| `LLM_BASE_URL` | provider default | OpenAI-compatible or Ollama endpoint |
| `DATA_DIR` | `./data` | SQLite and uploads |
| `DEMO_MODE` | `true` | Enables reset, demo samples and tamper test controls |

The header always shows which engines are live: **PayPal mock** vs **PayPal sandbox**,
and **Rules engine** vs the configured model. No key means no fictional AI claims.

---

## How the money works

```text
Mandate ──► AI plan ──► Job ──► Mandate engine
                                   │
                     ┌─────────────┼──────────────┐
                     ▼             ▼              ▼
                   BLOCK     REQUIRE APPROVAL   ALLOW
                 (hard cap)   (human says yes)    │
                                   │              ▼
                                   └──────► PayPal Orders: create + authorize
                                                     (funds held, not captured)
                                                              │
                              submission ──► verifier ────────┤
                                                │             │
                                             revise        pass
                                                │             ▼
                                                │      partial capture
                                                │             │
                                                │             ▼
                                                └────── PayPal Payout to freelancer
                                                              │
                                                              ▼
                                                   SHA-256 hash-chained audit row
```

- **Authorize first.** The job total is one authorization. It is not revenue yet.
- **Capture after verification.** Each accepted milestone captures its own amount; the
  final capture closes the authorization.
- **Cancel voids the hold.** A cancelled job releases whatever was not captured.
- **Approvals do not bypass hard rules.** They only unlock a spend that is above the
  human threshold and otherwise legal.
- **All money is integer cents.** No floats touch balances.

See [docs/PAYPAL.md](docs/PAYPAL.md) for the exact API calls, sandbox setup, webhook
verification and live-mode guardrails.

---

## Mandate rules

| Rule | Behaviour |
| --- | --- |
| Budget | Blocks if spent + committed + pending + request exceeds the budget |
| Category | Blocks categories outside the mandate unless `any` |
| Expiry | Blocks after `expires_at` |
| Per transaction | Blocks a single request above the cap |
| Daily limit | Blocks authorizations whose 24-hour total would exceed the cap |
| Approval threshold | Requires a human approval for amounts **strictly greater than** the threshold |
| Approval binding | Single-use, tied to mandate + job + amount + category, with an expiry |
| Capture | Enforces remaining authorization, category and budget; does not re-trigger the approval threshold |

The engine is implemented in [`app/mandate.py`](app/mandate.py) and covered by
[`tests/test_mandate.py`](tests/test_mandate.py).

---

## Verification: evidence over vibes

The verifier is rules-first. It inspects the actual bytes of each deliverable:

- file format and extension
- image/PDF dimensions
- colour count in SVGs
- full-bleed background vs transparency
- placeholder terms (`lorem ipsum`, `placeholder`, `tbd`, ...)
- word counts and required keywords
- licence language and file counts
- SVG script elements (rejected)

An optional LLM can add qualitative judgment, but it **cannot turn a hard rule failure
into a pass**. If no model is configured, the UI says the qualitative review was
skipped instead of pretending otherwise.

The demo is engineered so you can see both sides:

| Package | Result | Why |
| --- | --- | --- |
| `concept-board.svg` | pass | 1000×700, 3+ directions, real rationale |
| `logo-draft.svg` | **revise** | 400×200, 11 colours, full-bleed, "PLACEHOLDER" + lorem ipsum |
| `logo-final.svg` | pass | 1200×400, 3 colours, transparent, real wordmark |
| `logo-final.svg` + `brand-guide.md` | pass | vector master + licence and usage notes |

---

## AG Grid audit log

The **Audit & money trail** panel is AG Grid Community v36, vendored at
`app/static/vendor/` so the demo has no CDN or Node dependency. It provides quick
filtering, a decision filter, sortable columns, an audit/payments tab switch, and a
plain HTML fallback if AG Grid cannot initialise.

Each audit row contains `prev_hash` and `hash`, where:

```text
hash = SHA-256(canonical_json(row_without_hash_and_prev_hash) + prev_hash)
```

The first row links to 64 zeroes. **Verify chain** recomputes every link. **Tamper
test** alters the newest row in the database on purpose, then the next verification
reports the first broken sequence number. The same check is available at
`GET /api/audit/verify`.

---

## API

Interactive OpenAPI docs: <http://127.0.0.1:8000/docs>. The UI uses the same public API
that a judge or an agent would.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Engine mode, LLM status, chain health |
| `GET` | `/api/state` | Full dashboard state |
| `POST` | `/api/command` | Parse an instruction, create a mandate + plan; `post:false` previews |
| `POST` | `/api/mandates` | Create a mandate from explicit fields |
| `POST` | `/api/jobs` | Post a job with an optional plan |
| `POST` | `/api/jobs/{id}/authorize` | Run the mandate engine, then authorize with PayPal |
| `POST` | `/api/jobs/{id}/cancel` | Void the remaining authorization |
| `POST` | `/api/approvals/{id}/resolve` | Approve or reject a threshold spend |
| `POST` | `/api/spend/check` | Spend simulator / policy dry run |
| `POST` | `/api/milestones/{id}/submissions` | Submit work |
| `POST` | `/api/submissions/{id}/verify` | Verify and settle if it passes |
| `POST` | `/api/milestones/{id}/release` | Human override release |
| `POST` | `/api/uploads` | Upload a deliverable |
| `GET` | `/api/audit/verify` | Recompute the hash chain |
| `POST` | `/api/audit/tamper` | Demo-only tamper test |
| `POST` | `/api/demo/reset` | Reset the demo database |
| `GET` | `/api/stream` | Server-sent events for live updates |
| `POST` | `/api/webhooks/paypal` | PayPal webhook receiver with signature verification |

Example:

```bash
curl -s http://127.0.0.1:8000/api/command \
  -H "Content-Type: application/json" \
  -d '{"text":"Get me a logo for my bakery, max $60, ask me before anything over $40.","post":false}'
```

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Coverage includes:

- strict threshold semantics, hard blocks, category/per-transaction/daily/expiry
- single-use approvals and amount/job binding
- capture limits against the remaining authorization
- planner budget-sum invariant across categories and budgets
- mock-PayPal end-to-end flow: block → approve → authorize → reject draft → release
- audit chain verification and tamper detection
- objective sample inspection

Smoke-test a running server:

```bash
python scripts/smoke.py
```

---

## Project structure

```text
app/
  main.py        FastAPI routes, SSE, uploads, webhooks
  service.py     Orchestration: command, jobs, approvals, verification, settlement
  mandate.py     The spend policy engine
  planner.py     Deterministic + optional LLM milestone planning
  verify.py      Rules-first verification and AI merge guardrail
  inspect.py     File metadata, colour, dimension and placeholder inspection
  paypal.py      Orders v2, Payouts, webhook verification, deterministic mock
  store.py       SQLite schema, financials, hash-chained audit
  nlp.py         Natural-language mandate parser
  llm.py         Optional OpenAI/Anthropic/Ollama client
  models.py      Pydantic models and enums
  config.py      Environment-driven settings
  static/        Vanilla UI, styles, app.js, vendored AG Grid
samples/         Demo deliverables engineered to pass or fail
tests/           pytest suite
docs/            Architecture, PayPal, demo script, Devpost copy
scripts/         Smoke test
```

---

## Hackathon track alignment

- **Best Use of Agentic Commerce** — an agent that can transact, with policy,
  approvals, verification and settlement as first-class primitives.
- **Best Use of PayPal + AI** — PayPal Orders authorizations, partial captures,
  Payouts and webhook verification wrapped around AI planning and verification.
- **Best Use of AG Grid** — the audit/payments grid is the money trail, not decoration;
  quick filter, decision filter and hash-linked rows.
- **Most Impactful** — a reusable safety layer for any agent that spends money.
- **Most Creative** — "authorize, don't release" as the core interaction.
- **Grand Prize** — working software, honest mock fallbacks, tests, docs and a
  sub-three-minute scripted demo.

---

## Safety and limitations

- Sandbox by default. Live PayPal is blocked behind `ALLOW_LIVE=1` **and** credentials.
- The mock adapter is deterministic and must never be confused with real money; the UI
  labels it clearly.
- The audit chain proves internal consistency, not external notarization. A production
  deployment would anchor hashes to an append-only external store.
- Uploaded files are stored locally and served with a restrictive content policy. A
  production deployment needs malware scanning, object storage and per-tenant isolation.
- This is a hackathon prototype, not a licensed escrow or money-transmission service.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [PayPal integration](docs/PAYPAL.md)
- [Demo script](docs/DEMO_SCRIPT.md)
- [Devpost submission copy](docs/DEVPOST.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

MIT — see [LICENSE](LICENSE).
