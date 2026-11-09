"""Planner invariants: milestones always add up to the budget."""

from __future__ import annotations

import pytest

from tests.support import DEMO_PROMPT, create_mandate, run

CATEGORIES = ["design", "writing", "development", "video", "audio", "research", "other"]
BUDGETS = [5_000, 6_000, 12_345]


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("budget_cents", BUDGETS)
def test_deterministic_plan_sums_exactly_to_budget(service, category, budget_cents):
    plan = run(
        service.planner.plan(
            brief=f"A {category} project for a small business.",
            category=category,
            budget_cents=budget_cents,
        )
    )

    assert plan.engine == "deterministic"
    assert plan.total_cents == budget_cents
    assert len(plan.milestones) >= 2
    assert all(milestone.amount_cents > 0 for milestone in plan.milestones)
    assert all(milestone.criteria for milestone in plan.milestones)


def test_command_preview_parses_the_demo_prompt(service):
    result = run(service.command(DEMO_PROMPT, post=False))

    assert result["mandate"]["budget_cents"] == 6_000
    assert result["mandate"]["approval_threshold_cents"] == 4_000
    assert result["parsed"]["category"] == "design"
    assert sum(item["amount_cents"] for item in result["plan"]["milestones"]) == 6_000
    assert "job" not in result


def test_create_job_rejects_a_plan_that_does_not_sum(service):
    mandate = create_mandate(service, budget_cents=6_000)
    plan = run(
        service.planner.plan(
            brief="Bakery logo", category="design", budget_cents=6_000
        )
    ).model_dump()
    plan["milestones"][0]["amount_cents"] += 1

    from app.models import CreateJobRequest
    import pytest as _pytest
    from app.service import ServiceError

    with _pytest.raises(ServiceError):
        run(
            service.create_job(
                CreateJobRequest(
                    mandate_id=mandate["mandate"]["id"],
                    amount_cents=6_000,
                    category="design",
                    plan=plan,
                )
            )
        )
