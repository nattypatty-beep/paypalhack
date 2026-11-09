"""Unit tests for the mandate engine.

The mandate is the product's safety boundary. These tests pin the important
guarantees: hard limits cannot be approved away, the threshold is strict, and an
approval is single-use and bound to one job, amount and category.
"""

from __future__ import annotations

from app.mandate import MandateEngine, SpendRequest
from app.models import CreateJobRequest, ResolveApprovalRequest
from app.util import iso_after

from tests.support import create_mandate, insert_mandate, run


def decide(service, mandate_id: str, amount_cents: int, **overrides):
    request = SpendRequest(
        mandate_id=mandate_id,
        amount_cents=amount_cents,
        currency=overrides.pop("currency", "USD"),
        category=overrides.pop("category", "design"),
        **overrides,
    )
    return MandateEngine(service.store).evaluate(request)


def test_allows_spend_under_threshold(service):
    mandate = insert_mandate(service.store)

    decision = decide(service, mandate["id"], 4_000)

    assert decision.verdict == "allow"
    assert decision.reason_codes == []
    assert decision.snapshot["projected_cents"] == 4_000


def test_threshold_is_strictly_greater_than(service):
    mandate = insert_mandate(service.store)

    at_threshold = decide(service, mandate["id"], 4_000)
    over_threshold = decide(service, mandate["id"], 4_001)

    assert at_threshold.verdict == "allow"
    assert over_threshold.verdict == "require_approval"
    assert over_threshold.reason_codes == ["approval_threshold"]
    assert over_threshold.approval is None


def test_budget_is_a_hard_block(service):
    mandate = insert_mandate(service.store, budget_cents=5_000)

    decision = decide(service, mandate["id"], 5_001)

    assert decision.verdict == "block"
    assert "budget_exceeded" in decision.reason_codes


def test_category_outside_mandate_is_blocked(service):
    mandate = insert_mandate(service.store, categories=("design",))

    decision = decide(service, mandate["id"], 1_000, category="video")

    assert decision.verdict == "block"
    assert "category_not_allowed" in decision.reason_codes


def test_per_transaction_limit_is_blocked(service):
    mandate = insert_mandate(
        service.store, budget_cents=10_000, per_txn_limit_cents=2_500
    )

    decision = decide(service, mandate["id"], 2_501)

    assert decision.verdict == "block"
    assert "per_transaction_limit" in decision.reason_codes


def test_expired_mandate_is_blocked(service):
    mandate = insert_mandate(
        service.store, expires_at=iso_after(-5), status="active"
    )

    decision = decide(service, mandate["id"], 1_000)

    assert decision.verdict == "block"
    assert "mandate_expired" in decision.reason_codes


def test_currency_mismatch_is_blocked(service):
    mandate = insert_mandate(service.store, currency="USD")

    decision = decide(service, mandate["id"], 1_000, currency="EUR")

    assert decision.verdict == "block"
    assert "currency_mismatch" in decision.reason_codes


def test_daily_limit_counts_authorizations(service):
    mandate = create_mandate(
        service, budget_cents=20_000, approval_threshold_cents=4_000, daily_limit_cents=5_000
    )
    mandate_id = mandate["mandate"]["id"]

    first_job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=3_000,
                category="design",
                brief="First small job",
            )
        )
    )
    first = run(service.authorize_job(first_job["job"]["id"]))
    assert first["status"] == "authorized"

    second_job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=3_000,
                category="design",
                brief="Second small job",
            )
        )
    )
    second = run(service.authorize_job(second_job["job"]["id"]))

    assert second["status"] == "blocked"
    assert "daily_limit" in second["decision"]["reason_codes"]


def test_approval_is_single_use(service):
    mandate = create_mandate(
        service, budget_cents=20_000, approval_threshold_cents=4_000
    )
    mandate_id = mandate["mandate"]["id"]

    job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=6_000,
                category="design",
                brief="Bakery logo",
            )
        )
    )
    job_id = job["job"]["id"]

    waiting = run(service.authorize_job(job_id))
    assert waiting["status"] == "approval_required"
    approval_id = waiting["approval"]["id"]

    service.resolve_approval(
        approval_id, ResolveApprovalRequest(approve=True, note="approved for the demo")
    )
    authorized = run(service.authorize_job(job_id, approval_id))
    assert authorized["status"] == "authorized"
    assert service.store.get_approval(approval_id)["status"] == "consumed"

    second_job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=6_000,
                category="design",
                brief="Another logo",
            )
        )
    )
    reused = run(service.authorize_job(second_job["job"]["id"], approval_id))

    assert reused["status"] == "blocked"
    assert "approval_consumed" in reused["decision"]["reason_codes"]


def test_approval_cannot_be_reused_for_a_different_amount(service):
    mandate = create_mandate(
        service, budget_cents=30_000, approval_threshold_cents=4_000
    )
    mandate_id = mandate["mandate"]["id"]

    first_job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=6_000,
                category="design",
                brief="Authorized job",
            )
        )
    )
    waiting = run(service.authorize_job(first_job["job"]["id"]))
    approval_id = waiting["approval"]["id"]
    service.resolve_approval(approval_id, ResolveApprovalRequest(approve=True))

    other_job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=7_000,
                category="design",
                brief="Different amount",
            )
        )
    )
    decision = run(service.authorize_job(other_job["job"]["id"], approval_id))

    assert decision["status"] == "blocked"
    assert "approval_amount_mismatch" in decision["decision"]["reason_codes"]


def test_capture_cannot_exceed_the_authorization(service):
    mandate = create_mandate(
        service, budget_cents=20_000, approval_threshold_cents=4_000
    )
    mandate_id = mandate["mandate"]["id"]

    job = run(
        service.create_job(
            CreateJobRequest(
                mandate_id=mandate_id,
                amount_cents=6_000,
                category="design",
                brief="Escrowed job",
            )
        )
    )
    job_id = job["job"]["id"]
    waiting = run(service.authorize_job(job_id))
    service.resolve_approval(
        waiting["approval"]["id"], ResolveApprovalRequest(approve=True)
    )
    assert run(service.authorize_job(job_id, waiting["approval"]["id"]))["status"] == "authorized"

    too_much = decide(
        service,
        mandate_id,
        6_001,
        kind="capture",
        job_id=job_id,
        category="design",
    )
    allowed = decide(
        service, mandate_id, 6_000, kind="capture", job_id=job_id, category="design"
    )

    assert too_much.verdict == "block"
    assert "authorization_exhausted" in too_much.reason_codes
    assert allowed.verdict == "allow"
