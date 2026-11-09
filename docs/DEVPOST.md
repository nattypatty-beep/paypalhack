# Devpost submission copy

Copy the sections below into Devpost. Replace `YOUR_VIDEO_ID` with the published
video before submitting.

---

## Project name

**MandatePay**

## Tagline

**Agentic commerce with a leash.**

## Elevator pitch

MandatePay lets an AI agent hire freelancers inside a human mandate. It parses a
plain-English budget and approval rule, drafts an AI milestone plan, authorizes a
PayPal payment without capturing it, verifies delivered work against
machine-checkable criteria, and releases money only when the work passes. Every
decision lands in a tamper-evident AG Grid audit trail.

## Inspiration

Agentic commerce has an awkward gap. Giving an autonomous agent a checkout button
is either useless (a human approves every cent) or reckless (the agent has your
card). We wanted to build the missing middle: a reusable **safety layer for
agents that spend money**.

The insight is that a human approval should not be a blanket permission or a
manual click on every transaction. It should be a **scoped capability**: one job,
one amount, one category, one use, with a short expiry. And the money should not
move when the agent says it is done. It should move when the work can be checked.

PayPal is a natural fit because an authorization is already a hold. MandatePay
turns that primitive into a milestone escrow: authorize up front, capture only
after verification, pay the freelancer through Payouts.

## What it does

1. **Turns language into policy.**
   `Get me a logo for my bakery, max $60, ask me before anything over $40.`
   becomes a mandate with a $60 budget, a $40 approval threshold, a design
   category, an expiry, and optional per-transaction/daily limits.

2. **Drafts a verifiable plan.**
   The agent creates milestones with machine-checkable acceptance criteria:
   format, dimensions, palette size, transparency, placeholder text, word counts,
   keyword coverage, licence language and file counts.

3. **Enforces the mandate.**
   A $75 job is hard-blocked with `budget_exceeded`. No approval can override the
   budget. A $60 job is legal but above the threshold, so it pauses for a human.

4. **Uses approval as a capability.**
   Approving creates a single-use approval bound to the mandate, job, exact
   amount and category, with separate request and token expiries. It authorizes a
   PayPal hold; it does not release funds.

5. **Verifies evidence, not promises.**
   The verifier inspects the actual files. The demo's concept board passes. A
   400×200, 11-colour, full-bleed draft with "PLACEHOLDER" and lorem ipsum is
   rejected with per-criterion evidence. The final 1200×400, 3-colour,
   transparent vector passes. The brand kit passes only with licence language and
   multiple files.

6. **Settles milestone by milestone.**
   Each accepted milestone is a partial capture of the authorization. The
   freelancer is paid through PayPal Payouts. Cancelling voids the remaining
   hold.

7. **Leaves a receipt.**
   Every decision is an append-only audit row with `prev_hash` and `hash`. The
   AG Grid panel supports quick filtering, decision filters, sorting, and an
   audit/payments tab switch. A tamper-test button corrupts the newest row so the
   next verification reports the first broken sequence number.

## How we built it

**Backend:** Python 3.13, FastAPI, Pydantic, stdlib `sqlite3`, `httpx`. No ORM,
no message broker, no background worker. The entire app is one process and one
SQLite file.

**Payments:** PayPal Orders v2 (`intent=AUTHORIZE`), authorizations, partial
captures, void, Payouts, and webhook signature verification. A deterministic mock
implements the same state machine so the demo runs offline with no credentials.
Live money requires both real credentials and `ALLOW_LIVE=1`.

**AI:** A deterministic rules parser/planner is the baseline so the app never
depends on a model to function. An optional LLM layer supports OpenAI-compatible,
Anthropic and Ollama endpoints for planning and qualitative review. The verifier
is rules-first: an LLM may add a qualitative judgment, but it cannot turn a hard
rule failure into a pass. With no key, the UI says the qualitative review was
skipped.

**Frontend:** Vanilla JavaScript, HTML and CSS with no build step. Server-Sent
Events push state updates. AG Grid Community v36.3.0 is vendored locally (no CDN,
no Node), uses the Theming API, and falls back to a plain HTML table if grid
initialization fails.

**Audit:** SHA-256 hash chain over canonical JSON, verified by recomputing every
link. The genesis row links to 64 zeroes. The audit chain is not just a log; it
is a tamper-evident receipt of who allowed what, and why.

**Tests:** 53 pytest tests covering strict threshold semantics, hard blocks,
approval binding and single use, capture limits, the planner budget invariant,
the end-to-end API flow, audit verification/tamper detection, and objective file
inspection.

## Challenges

- **Making approvals safe without making them useless.** The first design treated
  approval as a boolean. That let a stale "yes" authorize a different job. We
  bound approvals to mandate, job, exact amount and category, made them
  single-use, and gave them two expiries: one to decide, one to use.
- **Keeping hard rules hard.** It is tempting to let an approval override
  everything. We separated the checks so an approval only satisfies the
  above-threshold case; budget, category, expiry and per-transaction limits stay
  hard.
- **Partial captures and settlement state.** Capture draws down an authorization
  that was already approved. Re-running the threshold check on capture would
  demand a second approval for money the human already authorized. We made
  captures enforce remaining authorization, category and budget, but not the
  threshold.
- **Verification that cannot be talked around.** LLMs are agreeable. We made
  rules authoritative, added a confidence floor, treated submission text as
  untrusted data in prompts, and explicitly discard any model attempt to pass a
  failed objective criterion.
- **An honest demo.** The easiest hackathon shortcut is to pretend the mock is
  real. We labelled every engine in the header, made the mock enforce the same
  state machine, and made the no-LLM path say "qualitative review skipped".
- **No Node, no CDN.** Vendoring AG Grid and using the Theming API avoided a
  build step and a network dependency. We hit a legacy-theme error and fixed it
  by removing the old CSS links.
- **Tamper-evident, not tamper-proof.** We are explicit that the chain proves
  internal consistency, not external notarization. A production deployment would
  anchor the head to an append-only external store.

## Accomplishments we are proud of

- A complete, working flow in under three minutes: block → approve → authorize →
  reject a weak draft → accept the final → capture → payout → verify the chain.
- A real safety model, not a UI mock: integer cents, hard limits, scoped
  approvals, authorization draw-down and idempotent retries.
- Objective verification with per-criterion evidence a human can audit.
- A polished, dependency-light UI that runs offline and still uses AG Grid for
  the money trail.
- 53 passing tests and a smoke test that a judge can run against a live server.
- Documentation that states the limitations plainly.

## What we learned

- **The interesting problem is not payment authorization; it is the mandate.**
  The hard part is deciding what an agent may do before it acts, and making that
  decision legible afterwards.
- **Approvals need scope, not just consent.** A yes without an amount, job and
  expiry is not a control.
- **Verification needs evidence.** "Looks good to me" is not an acceptance
  criterion. Dimensions, palettes, placeholders and word counts are.
- **AI should be optional in the safety path.** The system must remain correct
  when the model is slow, wrong, or absent.
- **Honest fallbacks build trust.** Labelling the mock and the rules engine made
  the demo more convincing, not less.

## What is next

- Anchor the audit chain head to an external append-only store or timestamping
  service.
- Add real user accounts (beyond the optional shared Basic auth), tenants and
  per-tenant upload isolation.
- Add a reconciliation worker for the capture→payout gap.
- Support refunds/disputes and multi-currency mandates.
- Add more inspections (fonts, accessibility, contrast, code tests) and more
  category templates.
- Ship a hosted sandbox with a public judge link.
- Explore policy templates for common agent tasks beyond freelancing: cloud
  spend, ad budgets, data purchases, API credits.

## Built with

`python` `fastapi` `pydantic` `sqlite` `httpx` `uvicorn` `javascript` `html`
`css` `ag-grid` `paypal-orders-v2` `paypal-payouts` `paypal-webhooks` `openai`
`anthropic` `ollama` `pytest`

## Try it out

Repository: <https://github.com/nattypatty-beep/paypalhack>

```bash
git clone https://github.com/nattypatty-beep/paypalhack.git
cd paypalhack
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, click **Run full demo**, and approve the $60 hold
when the modal appears. No credentials or network access are required.

PayPal sandbox setup: [docs/PAYPAL.md](PAYPAL.md).

## Demo video

`https://youtu.be/YOUR_VIDEO_ID`

Runtime: under three minutes. No live money. The header shows **PayPal mock** and
**Rules engine** unless credentials are configured.

## Track fit

| Track / prize | Why this project fits |
| --- | --- |
| **Best Use of Agentic Commerce** | The agent plans, hires, spends, verifies and settles, with policy, approval and audit as first-class primitives. |
| **Best Use of PayPal + AI** | Orders authorizations, partial captures, void, Payouts and webhook verification wrap AI planning and AI-assisted verification. |
| **Best Use of AG Grid** | The audit/payments grid is the product's money trail: filterable, sortable, decision-filtered, hash-linked rows, with an HTML fallback. |
| **Most Creative** | "Authorize, don't release" as the core interaction; approval as a scoped, expiring, single-use capability. |
| **Most Impactful** | A reusable safety layer for any agent that spends money, demonstrated on freelancer procurement. |
| **Grand Prize** | Working software, honest offline fallbacks, real PayPal integration, tests, docs, and a sub-three-minute demo. |

## Judging notes

- **It runs without credentials.** Mock PayPal and the rules planner/verifier are
  deterministic. The same code switches to sandbox by adding credentials.
- **The demo is real code.** The Demo director calls the public API. Every step
  is reproducible with `curl` or the OpenAPI docs at `/docs`.
- **Nothing is captured before verification.** The approval only authorizes a
  hold. Captures happen per accepted milestone.
- **A failed verification is shown, not hidden.** The placeholder draft is
  engineered to fail on objective criteria, and the money stays held.
- **The audit chain is verifiable.** `GET /api/audit/verify` recomputes every
  link. The tamper test mutates the database and reports the first broken row.
- **Limitations are documented.** This is a hackathon prototype, not a licensed
  escrow or money-transmission service. See
  [SECURITY.md](../SECURITY.md) and
  [ARCHITECTURE.md](ARCHITECTURE.md#11-deliberate-non-goals).

## Optional shorter description

MandatePay is an AI procurement agent with a leash. A human sets a budget,
category and approval threshold in plain English. The agent drafts a milestone
plan, asks for a human approval when needed, authorizes a PayPal hold, verifies
the delivered files against objective criteria, and captures funds only for
accepted work. A rejected draft leaves the money held. Every decision is
SHA-256 hash-chained and shown in AG Grid. It runs offline in mock mode and
switches to PayPal sandbox with credentials.
