# Contributing to MandatePay

Thanks for taking a look. MandatePay is a hackathon prototype with a small,
dependency-light codebase. Contributions are welcome, especially around the
safety invariants, verification criteria and documentation.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md)
and to license your contributions under the [MIT License](LICENSE).

---

## 1. Set up

**Requirements:** Python 3.11+ (tested on 3.13). No Node, no database server.

```bash
git clone https://github.com/YOUR_ORG/mandatepay.git
cd mandatepay
python -m venv .venv

# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The default configuration uses mock PayPal and the
deterministic rules planner/verifier. No credentials are required.

Run the tests before you change anything:

```bash
python -m pytest -q
python -m compileall -q app tests scripts
```

---

## 2. Project map

```text
app/main.py        FastAPI routes, SSE, uploads, webhooks, error handlers
app/service.py     Orchestration: commands, jobs, approvals, verify, settle
app/mandate.py     Spend policy engine
app/planner.py     Deterministic + optional LLM milestone planning
app/verify.py      Rules-first verification and AI merge guardrail
app/inspect.py     File facts: format, dimensions, colours, text, hashes
app/paypal.py      Orders v2, Payouts, webhook verification, deterministic mock
app/store.py       SQLite schema, financials, hash-chained audit
app/nlp.py         Plain-English mandate parser
app/llm.py         Optional OpenAI/Anthropic/Ollama client
app/models.py      Pydantic models and enums
app/config.py      Environment-driven settings
app/static/        Vanilla UI, styles, app.js, vendored AG Grid
samples/           Demo deliverables engineered to pass or fail
tests/             pytest suite
docs/              Architecture, PayPal, demo script, Devpost copy
scripts/smoke.py   Smoke test for a running server
```

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
[docs/PAYPAL.md](docs/PAYPAL.md) for payment details.

---

## 3. Rules of the codebase

These are invariants, not style preferences. A PR that breaks one needs a very
good reason and a test that proves the new behaviour is safe.

### Money

- **All money is integer cents.** Never use floats for amounts, balances or fees.
- Format only through `app/money.py`.
- A capture may never exceed the remaining authorized amount.
- A hard budget block may never be overridden by an approval.
- Captures do not re-trigger the approval threshold; they draw down an existing
  authorization.

### Approvals

- An approval is bound to mandate, job, exact amount and category.
- It is single-use and has request and token expiries.
- A pending approval counts against the mandate budget (`pending_cents`).
- Re-authorizing an `awaiting_approval` job must return the existing approval, not
  create a second one.

### Verification

- Rules are authoritative. An LLM may not turn a failed objective criterion into
  a pass.
- A missing model must produce an honest "qualitative review skipped" result.
- Every criterion must include evidence a human can read.
- Malformed files must produce a failed inspection, never a crash.

### Audit

- Every state transition that matters writes an audit row.
- Audit rows are append-only. Do not update or delete them outside the explicit
  demo reset/tamper tools.
- `prev_hash` and `hash` must remain consistent with the canonical JSON formula
  in `app/store.py`.

### Frontend

- The server is the source of truth. Do not implement policy decisions client
  side.
- The UI must label the active engines honestly: **PayPal mock** vs
  **PayPal sandbox**, **Rules engine** vs the configured model.
- Keep the no-build-step constraint. No Node, no bundler, no CDN requirement.
- AG Grid must keep its plain HTML fallback.

---

## 4. Tests

Add tests with behaviour changes. The suite is organized by area:

| File | Focus |
| --- | --- |
| `tests/test_mandate.py` | threshold semantics, hard blocks, approval binding, captures |
| `tests/test_planner.py` | plan structure and budget-sum invariant |
| `tests/test_api_flow.py` | end-to-end API flow in mock mode |
| `tests/test_audit.py` | hash chain and tamper detection |
| `tests/test_inspect.py` | objective file inspection |

Use the fixtures in `tests/conftest.py` and `tests/support.py`; they create a
temporary data directory and an isolated app. Never point tests at the real
`data/` directory.

```bash
python -m pytest -q
python -m pytest tests/test_mandate.py -q
python -m pytest -q -k approval
```

Smoke-test a running server:

```bash
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

The script resets the demo before and after, so it is safe to run against a local
instance.

---

## 5. Style

- Python 3.11+ syntax, type hints where they add clarity.
- Formatting/lint config is in `pyproject.toml` (Ruff, line length 100). Ruff is
  optional; match the surrounding style if you do not run it.
- Keep comments explanatory, not decorative.
- Prefer small functions with explicit names over clever abstractions.
- Frontend: vanilla JS in the existing IIFE style, no framework.
- Documentation: plain Markdown, relative links, working code fences.

No new runtime dependencies unless they earn their place. The offline demo is a
feature; do not add a package that requires network access or a build step at
startup.

---

## 6. Pull request checklist

- [ ] The change preserves the invariants in section 3.
- [ ] `python -m pytest -q` passes.
- [ ] `python -m compileall -q app tests scripts` passes.
- [ ] New behaviour has a test.
- [ ] User-facing changes update the README or docs.
- [ ] No secrets, real customer data or generated databases are committed.
- [ ] The UI still labels mock/sandbox and rules/LLM honestly.
- [ ] The demo still completes end to end (or the PR explains why it changed).

A good PR description answers:

1. What problem does this solve?
2. What is the user-visible change?
3. What did you test, and how?
4. Does it affect money, approvals, verification or audit? If so, why is it safe?

---

## 7. Good first issues

- Add a new planner template (writing, video, audio, development and research
  already exist).
- Add an inspection fact (font names, contrast ratio, PDF page count).
- Add a criterion type with tests and documentation.
- Improve the audit grid (CSV export, column presets).
- Add a webhook event mapping with a signed-sample test.
- Improve accessibility (focus states, modal focus trap, reduced motion).
- Add a Dockerfile or a one-command launcher.
- Improve the demo script or record a captioned version.

If you are unsure where to start, open an issue with the area you are interested
in.

---

## 8. Reporting bugs

Include:

- OS and Python version;
- whether PayPal mode was mock or sandbox;
- whether an LLM was configured;
- the exact prompt/API request;
- the expected and actual result;
- relevant server logs with secrets removed.

For security issues, **do not open a public issue**. Follow
[SECURITY.md](SECURITY.md).

---

## 9. License

By contributing, you agree that your contributions are licensed under the MIT
License. See [LICENSE](LICENSE).
