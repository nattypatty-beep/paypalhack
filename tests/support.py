"""Shared helpers for the MandatePay test suite."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config import ROOT, Settings
from app.models import CreateMandateRequest
from app.util import iso_after, new_id

DEMO_PROMPT = "Get me a logo for my bakery, max $60, ask me before anything over $40."


def make_settings(tmp_path: Path) -> Settings:
    """Settings that never touch the network and always use the demo samples."""

    return Settings(
        data_dir=tmp_path / "data",
        samples_dir=ROOT / "samples",
        demo_mode=True,
        paypal_force_mock=True,
        paypal_payouts_enabled=True,
        llm_provider="none",
    )


def run(coro):
    return asyncio.run(coro)


def create_mandate(service, **overrides: Any) -> dict:
    values: dict[str, Any] = {
        "principal": "you",
        "currency": "USD",
        "budget_cents": 10_000,
        "approval_threshold_cents": 4_000,
        "categories": ["design"],
        "expires_in_days": 7,
        "raw_text": DEMO_PROMPT,
    }
    values.update(overrides)
    return run(service.create_mandate(CreateMandateRequest(**values)))


def insert_mandate(
    store,
    *,
    budget_cents: int = 10_000,
    approval_threshold_cents: int = 4_000,
    categories: tuple[str, ...] = ("design",),
    per_txn_limit_cents: int | None = None,
    daily_limit_cents: int | None = None,
    expires_at: str | None = None,
    status: str = "active",
    currency: str = "USD",
) -> dict:
    """Insert a mandate directly, without running the planner (unit tests)."""

    return store.insert_mandate(
        {
            "id": new_id("mnd"),
            "principal": "you",
            "currency": currency,
            "budget_cents": budget_cents,
            "approval_threshold_cents": approval_threshold_cents,
            "per_txn_limit_cents": per_txn_limit_cents,
            "daily_limit_cents": daily_limit_cents,
            "categories": list(categories),
            "status": status,
            "raw_text": "test mandate",
            "created_at": iso_after(0),
            "expires_at": expires_at or iso_after(60 * 24 * 7),
        }
    )


def submit_and_verify(client, milestone_id: str, quality: str) -> dict:
    submission = client.post(
        f"/api/demo/milestones/{milestone_id}/submit", json={"quality": quality}
    )
    assert submission.status_code == 200, submission.text
    submission_id = submission.json()["id"]
    verified = client.post(f"/api/submissions/{submission_id}/verify")
    assert verified.status_code == 200, verified.text
    return verified.json()
