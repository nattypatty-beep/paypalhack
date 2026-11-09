# Demo script (under 3 minutes)

This is a shot list, not a screenplay. The target runtime is **2:30–2:45**. The
demo runs entirely offline in mock mode, so there is no live payment risk and no
network dependency during recording.

> The story: an AI agent wants to hire a freelancer. The human gives it a budget
> and an approval threshold. The agent tries to overspend and is blocked. It
> revises, asks the human, gets a hold authorized, delivers work, fails one
> verification, fixes it, and gets paid **only after** the work passes. Every
> step lands in a hash-chained audit log.

---

## Before recording

1. Start the app:

   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

2. Open <http://127.0.0.1:8000> in a clean browser window.
   - Use a 1080p or larger window; the dashboard is wide.
   - Zoom 100% (or 110% if text looks small on video).
   - Close DevTools, bookmarks bar and unrelated tabs.
3. Confirm the header says **PayPal mock** and **Rules engine**. This is honest
   and expected without credentials. Do **not** configure credentials just for
   the video unless you specifically want to show sandbox mode.
4. Click **Reset demo** once. The audit log should be at 1 row
   (`app.started`), or 2 after the reset row.
5. Do a dry run. The full demo pauses at the approval modal and waits for you.

**Recording tip:** record the window, not the whole desktop. Keep the mouse still
between actions. The demo director already scrolls to the relevant panels.

---

## Timeline

### 0:00–0:12 — Hook

Show the dashboard, then the README tagline if you have it on screen.

> "AI agents can now plan work and click 'pay'. The hard part is letting them do
> that without giving them a blank cheque. This is MandatePay: agentic commerce
> with a leash."

Point at the mandate bar: **$60 budget**, **approval over $40**.

### 0:12–0:35 — Mandate + hard block

Click **Run full demo** in the Demo director. The director resets, then:

- **Step 1** parses the instruction into budget, threshold and category, and
  drafts three milestones totaling $60.
- **Step 2** creates a **$75 rush job** and asks the mandate engine to authorize
  it. It is **hard-blocked** with `budget_exceeded`.

> "The agent tried to spend $75 against a $60 mandate. Notice what did not
> happen: there is no approval button for this. A human approval can unlock a
> spend above the threshold, but it can never override the budget."

Show the blocked job and the red audit row. Do not dwell.

### 0:35–1:00 — The human check

The director posts the real **$60 job**. It is inside the budget but above the
$40 threshold, so the **approval modal** opens.

> "Sixty dollars is legal, but it is above the line the human drew. The agent
> stops. Nothing is captured. Nothing is even authorized yet."

Point at the modal fields: amount, job, rule.

> "This approval is a capability, not a blank cheque: one job, one amount, one
> category, one use, and it expires."

Type a short note if you like (`bakery rebrand`) and click **Approve &
authorize**.

> "Approving authorizes a PayPal hold. It does not release money."

Wait for the job to show **authorized**. The director continues automatically.

### 1:00–1:25 — Concept passes, draft fails

**Step 4** submits `concept-board.svg`. It passes: format, dimensions, three
directions, palette, rationale. The verifier releases the first **$15** milestone
and pays the freelancer.

**Step 5** submits `logo-draft.svg`. It is 400×200, 11 colours, full-bleed, and
contains the word **PLACEHOLDER** plus lorem ipsum. The verdict is **revise**.

> "This is the important part. The draft is not rejected by a vibe check. The
> verifier read the bytes: resolution, palette, transparency and placeholder
> text. The money is still held."

Show the failed criteria and evidence in the verification studio.

### 1:25–1:55 — Final passes, brand kit passes

**Step 6** submits `logo-final.svg` (1200×400, 3 colours, transparent, real
wordmark). It passes and releases the second milestone.

**Step 7** submits `logo-final.svg` + `brand-guide.md`. The handoff includes
licence language and two files. It passes, captures the final amount and pays the
freelancer.

> "Three captures, three payouts, and the job is complete. The agent never had
> the ability to move money the human did not authorize."

### 1:55–2:20 — Audit trail

The director scrolls to **Audit & money trail** and verifies the chain.

> "Every decision is an append-only row, and every row contains the SHA-256 hash
> of the previous row. Twenty-three rows, zero breaks. This is AG Grid, so a
> judge can filter by decision or search for a job."

Click the **Payments** tab briefly. Show authorization, captures and payouts.

Optional 10 seconds: click **Tamper test**.

> "If someone edits the database directly, the next verification reports the
> first broken row. The receipt is not just a log; it is tamper-evident."

Click **Reset demo** to leave the app in a clean state if you continue recording.

### 2:20–2:40 — Close

Return to the top of the page.

> "MandatePay is a safety layer for any agent that spends money: machine-enforced
> limits, human approval as a scoped capability, authorize-don't-release
> settlement, objective verification, and a tamper-evident receipt. It runs
> offline in mock mode, and the same code switches to PayPal sandbox Orders,
> Authorizations, Captures and Payouts. Agentic commerce with a leash."

End on the MandatePay title or the completed dashboard.

---

## Lean version (90 seconds)

If the submission limit is tight, cut:

- the concept pass (mention it in one sentence);
- the payments tab;
- the tamper test.

Keep the **$75 block**, the **approval modal**, the **draft revise**, the
**final pass**, and the **audit verification**. Those five beats carry the whole
idea.

---

## Narration cheat sheet

| Beat | One line |
| --- | --- |
| Hook | "Agentic commerce with a leash." |
| Mandate | "Budget, category, expiry, and a human-approval threshold." |
| Block | "Approval can unlock a threshold, never a hard budget cap." |
| Approval | "One job, one amount, one use, one expiry." |
| Authorize | "A hold, not a payment." |
| Draft | "The verifier reads the bytes, not the promise." |
| Release | "Capture only after acceptance." |
| Payout | "The freelancer is paid from the captured milestone." |
| Audit | "Every row hashes the row before it." |
| Close | "A reusable safety layer for any spending agent." |

---

## Manual fallback (no demo director)

If the Demo director misbehaves on the recording machine, use the UI directly:

1. Enter the prompt:
   `Get me a logo for my bakery, max $60, ask me before anything over $40.`
   and preview the mandate/plan.
2. Create the job from the plan.
3. Authorize it. The approval modal appears; approve it.
4. In the verification studio, pick the first milestone and the
   **Concept board** package, submit and verify.
5. Pick the **Logo master** milestone, use the **Draft logo** package, submit and
   verify (expect `revise`).
6. Use the **Final logo** package on the same milestone, submit and verify.
7. Use the **Brand kit** package on the last milestone, submit and verify.
8. Click **Verify chain** in the audit panel.

API-only fallback:

```bash
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

The smoke script exercises the mandate parse, the hard block, and chain health.

---

## Common recording mistakes

- **Clicking Step while Run full demo is active.** Use one or the other.
- **Rejecting the approval.** The director stops by design; reset and run again.
- **Recording before the demo resets.** Start from **Reset demo** so the audience
  sees the full chain, not a partial one.
- **Showing a broken chain by accident.** The tamper test intentionally breaks
  it. Reset before the next take.
- **Claiming a real LLM review without a key.** The UI says the qualitative
  review was skipped. That is the honest, intended behaviour.
- **Claiming live money.** The header says **PayPal mock**. Say "mock" or
  "sandbox" out loud.

---

## What to put under the video

```text
MandatePay — agentic commerce with a leash.
An AI procurement agent hires freelancers inside a human mandate, authorizes
PayPal funds without capturing them, verifies work against machine-checkable
criteria, and releases money milestone by milestone.

Runs offline in mock mode: <repo URL>
Stack: Python + FastAPI + SQLite + vanilla JS + AG Grid + PayPal REST.
```
