# Pull request

## What changed

<!-- A short description of the problem and the change. -->

## Why

<!-- Link an issue if there is one. Explain the user-visible or safety impact. -->

## How it was tested

<!-- Commands, manual steps, screenshots. -->

```bash
python -m pytest -q
python -m compileall -q app tests scripts
```

## Money, approvals, verification or audit impact

<!--
Does this touch the mandate engine, approvals, captures, Payouts, verification
or the audit chain? If yes, explain why the change is safe. If no, say "none".
-->

## Checklist

- [ ] The invariants in `CONTRIBUTING.md` are preserved.
- [ ] Tests added or updated for behaviour changes.
- [ ] `python -m pytest -q` passes.
- [ ] `python -m compileall -q app tests scripts` passes.
- [ ] Docs/README updated where needed.
- [ ] No secrets, real customer data or generated databases committed.
- [ ] UI labels mock/sandbox and rules/LLM honestly.
- [ ] Demo still completes end to end, or the change is explained.
