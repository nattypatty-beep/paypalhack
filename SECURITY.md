# Security policy

MandatePay is a hackathon prototype that demonstrates how an AI spending agent
can be constrained by a human mandate. It is **not** a production payment,
escrow or money-transmission service.

We take the safety model seriously even though the demo is small. This document
describes what is protected, what is deliberately out of scope, and how to report
a problem.

---

## Supported versions

| Version | Supported |
| --- | --- |
| `main` (latest commit) | Yes |
| Releases/tags | Best effort |
| Forks and old snapshots | No |

The project is under active hackathon development. Security fixes land on `main`.

---

## Reporting a vulnerability

**Please do not open a public issue for a vulnerability.**

Use GitHub's private vulnerability reporting:

1. Open the repository on GitHub.
2. Go to **Security → Advisories → Report a vulnerability**.
3. Include:
   - affected commit/version;
   - reproduction steps or a proof of concept;
   - impact (money, approval bypass, audit integrity, data exposure);
   - whether PayPal mock or sandbox mode was active;
   - any suggested fix.

If private reporting is unavailable, contact the repository maintainers through
the contact method on their GitHub profile and ask for a private channel. Do not
include secrets or personal data in a public report.

We aim to acknowledge reports within 72 hours and to ship a fix or a documented
mitigation as soon as is practical for a hackathon project.

---

## Security model

### What the app is trying to protect

1. **The human's budget and limits.** An agent must not be able to spend outside
   the mandate, even if it controls the API calls.
2. **Approval scope.** A human "yes" must not become a reusable, amount-agnostic
   permission.
3. **Settlement.** Money must not be captured before verification, and a capture
   must not exceed the authorization.
4. **Audit integrity.** A decision must not be silently edited after the fact.
5. **Credentials and uploaded files.** PayPal secrets and user files should not
   leak or execute.

### Trust boundaries

| Boundary | Trusted? | Notes |
| --- | --- | --- |
| Browser UI | No | The server re-checks every action; client state is not authoritative |
| Agent/API caller | No | The mandate engine is the enforcement point |
| LLM output | No | Rules are authoritative; prompts treat submission text as untrusted |
| Submitted files | No | Parsed defensively; SVG scripts/DTD rejected; no execution |
| PayPal responses | Partially | Status/IDs are stored, but amounts are constrained locally |
| SQLite file | Yes, locally | Anyone with write access can tamper; the chain detects it |
| Environment/.env | Yes | Contains secrets; never committed |

---

## Controls in place

### Mandate enforcement

- Hard blocks for inactive/expired mandates, currency, category, per-transaction
  and daily limits, and projected budget.
- Approvals cannot override hard blocks.
- Approvals are bound to mandate, job, exact amount and category, are single-use,
  and have decision/token expiries.
- Pending approvals count against the budget.
- Capture is limited to the remaining authorization and re-checks category and
  budget.
- Amounts are integer cents; no float arithmetic.

### Payments

- Sandbox by default.
- Live mode requires both real credentials and `ALLOW_LIVE=1`.
- `PAYPAL_FORCE_MOCK=1` always wins.
- Stable `PayPal-Request-Id`/sender IDs for idempotency.
- OAuth tokens are held in memory only.
- PayPal errors are recorded and surfaced; the app does not silently retry a real
  payment.

### Verification

- Rules run before any model.
- An LLM may not convert a failed objective criterion into a pass.
- Low-confidence model judgments are ignored.
- LLM prompts instruct the model to treat submission text as untrusted data.
- A missing model produces an explicit "skipped" result.
- Malformed files fail inspection instead of crashing the verifier.

### Uploads

- Extension allowlist (`svg`, images, PDF, text, archives, audio/video, docs).
- Size limit (`MAX_UPLOAD_MB`, default 8 MB).
- Filename reduced to its basename and length-limited.
- Files stored under `data/uploads/` with generated IDs, not user paths.
- SVG DTD/entity declarations and `<script>` elements are rejected as invalid.
- Uploaded content is served with `Content-Security-Policy: default-src 'none'`
  and `X-Content-Type-Options: nosniff`.

### Audit

- Append-only rows with `prev_hash` and SHA-256 `hash`.
- Insert + previous-hash read happen in one SQLite transaction.
- Verification recomputes every link and reports the first broken row.
- Tamper test is restricted to demo mode.

### HTTP

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cache-Control: no-store` on API responses
- JSON error handlers do not leak stack traces to clients.

---

## Known limitations and accepted risks

These are intentional for the hackathon. A production deployment must address
them.

1. **No authentication or authorization.** The app assumes a single local
   principal. Anyone who can reach the port can act as the human, including
   approving spends. Do not expose it to the internet without an authenticating
   reverse proxy and per-user isolation.
2. **No CSRF protection.** The API is same-origin and unauthenticated; adding
   cookie auth without CSRF tokens would be unsafe.
3. **No global Content-Security-Policy on the HTML shell.** The app uses inline
   styles/scripts? It serves static files; a production deploy should add a
   strict CSP and a nonce/hash policy.
4. **No malware scanning or image decompression limits.** An 8 MB file can still
   be adversarial. Production needs scanning, decoding limits and object storage.
5. **No tenant isolation.** A single SQLite database and upload directory hold
   all data.
6. **The audit chain is tamper-evident, not tamper-proof.** An attacker with
   write access can rewrite the whole chain. Production should anchor the head to
   an append-only external store or timestamping service.
7. **No encryption at rest.** SQLite and uploads are plain files.
8. **No reconciliation worker.** A crash between capture and payout can leave a
   captured payment without a payout. The audit trail records the gap; production
   needs a retry/reconciliation job.
9. **No refund/dispute/chargeback flow.** Cancelling voids the remaining
   authorization but does not refund captured money.
10. **LLM prompt injection is mitigated, not eliminated.** The model can still be
    influenced for qualitative criteria; it cannot override objective rules.
11. **Live PayPal is a foot-gun.** `ALLOW_LIVE=1` is an explicit opt-in, but a
    misconfiguration can still move real money. Use sandbox credentials only.
12. **No legal escrow.** Authorizations and captures are PayPal primitives, not a
    licensed escrow arrangement.

---

## Safe deployment checklist

If you deploy this beyond localhost:

- [ ] Put it behind an authenticating reverse proxy (SSO or basic auth at
      minimum).
- [ ] Add CSRF protection if you introduce cookie-based sessions.
- [ ] Serve over HTTPS only.
- [ ] Set a strict CSP and review third-party assets.
- [ ] Keep `ALLOW_LIVE=0` unless you have a written reason and a small test
      budget.
- [ ] Use sandbox PayPal credentials for any public demo.
- [ ] Set `DEMO_MODE=false` to disable reset and tamper endpoints.
- [ ] Restrict uploads to trusted users or add scanning.
- [ ] Back up and monitor the SQLite file; add external audit anchoring.
- [ ] Add rate limiting and request size limits at the proxy.
- [ ] Review `FREELANCER_EMAIL`; never send sandbox payouts to a real address.
- [ ] Rotate any credential that was ever placed in `.env` or a screenshot.
- [ ] Confirm the MIT license and repository visibility.

---

## Secret handling

- Never commit `.env`; only `.env.example` is tracked.
- PayPal client secrets and LLM API keys are read from the environment.
- Do not paste secrets into the Devpost description, screenshots, logs, issues or
  the demo video.
- If a secret leaks, revoke it in the PayPal/OpenAI/Anthropic dashboard
  immediately; rotating the value in `.env` is not enough once it is public.
- The audit log intentionally records PayPal IDs, not credentials or tokens.

---

## Dependency security

- Runtime dependencies are pinned by compatible ranges in `requirements.txt`;
  install with a lockfile or `pip freeze` for reproducible production builds.
- AG Grid Community is vendored at a fixed version under
  `app/static/vendor/`; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Run `pip install -r requirements-dev.txt` in a virtual environment and use
  `pip-audit` or Dependabot in a production fork.
- Treat new dependencies as a security decision, not a convenience.

---

## Disclosure policy

We will credit reporters who want credit, once a fix is available. Please give us
a reasonable window before public disclosure. This is a volunteer hackathon
project; we will be transparent about what we can and cannot fix in time.
