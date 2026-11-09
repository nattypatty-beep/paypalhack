"""End-to-end API flow against the deterministic mock PayPal adapter."""

from __future__ import annotations

from tests.support import DEMO_PROMPT, submit_and_verify


def test_health_reports_mock_engines(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["paypal_mode"] == "mock"
    assert body["llm_enabled"] is False
    assert body["audit_ok"] is True


def test_full_job_lifecycle(client):
    # 1. The agent turns natural language into a mandate + plan (preview only).
    preview = client.post(
        "/api/command", json={"text": DEMO_PROMPT, "principal": "you", "post": False}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["mandate"]["budget_cents"] == 6_000
    assert body["mandate"]["approval_threshold_cents"] == 4_000
    assert body["parsed"]["category"] == "design"
    assert "job" not in body
    plan = body["plan"]
    total = sum(milestone["amount_cents"] for milestone in plan["milestones"])
    assert total == 6_000
    mandate_id = body["mandate"]["id"]

    # 2. The agent tries to overspend. The hard budget cap blocks it.
    probe = client.post(
        "/api/jobs",
        json={
            "mandate_id": mandate_id,
            "title": "Overspend probe",
            "brief": "A rush job the mandate cannot afford.",
            "category": "design",
            "amount_cents": 7_500,
        },
    )
    assert probe.status_code == 200, probe.text
    probe_decision = client.post(f"/api/jobs/{probe.json()['job']['id']}/authorize", json={})
    assert probe_decision.status_code == 200, probe_decision.text
    assert probe_decision.json()["status"] == "blocked"
    assert "budget_exceeded" in probe_decision.json()["decision"]["reason_codes"]

    # 3. The revised $60 job needs a human because it is above $40.
    created = client.post(
        "/api/jobs",
        json={
            "mandate_id": mandate_id,
            "title": plan["title"],
            "brief": body["parsed"]["job_brief"],
            "category": "design",
            "amount_cents": total,
            "plan": plan,
        },
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    job_id = job["id"]
    milestones = job["milestones"]
    assert len(milestones) == 3
    assert [milestone["amount_cents"] for milestone in milestones] == [1_500, 2_500, 2_000]

    waiting = client.post(f"/api/jobs/{job_id}/authorize", json={})
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "approval_required"
    approval = waiting.json()["approval"]
    assert approval["amount_cents"] == 6_000

    still_waiting = client.post(
        f"/api/jobs/{job_id}/authorize", json={"approval_id": approval["id"]}
    )
    assert still_waiting.status_code == 200
    assert still_waiting.json()["status"] == "approval_required"
    assert still_waiting.json()["approval"]["id"] == approval["id"]

    resolved = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        json={"approve": True, "note": "Approved for the demo."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "approved"

    authorized = client.post(
        f"/api/jobs/{job_id}/authorize", json={"approval_id": approval["id"]}
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["status"] == "authorized"
    assert authorized.json()["job"]["status"] == "authorized"

    # 4. Concept board passes, placeholder draft fails, final logo passes.
    concept = submit_and_verify(client, milestones[0]["id"], "concept")
    assert concept["verification"]["verdict"] == "pass"
    assert concept["release"]["status"] == "released"

    draft = submit_and_verify(client, milestones[1]["id"], "draft")
    assert draft["verification"]["verdict"] == "revise"
    assert draft["release"] is None
    assert {"dimensions", "palette", "placeholder"} & set(draft["verification"]["failed"])

    final = submit_and_verify(client, milestones[1]["id"], "final")
    assert final["verification"]["verdict"] == "pass"
    assert final["release"]["status"] == "released"

    brandkit = submit_and_verify(client, milestones[2]["id"], "brandkit")
    assert brandkit["verification"]["verdict"] == "pass"
    assert brandkit["release"]["status"] == "released"

    # 5. Everything is released, paid out, completed and hash-chained.
    state = client.get("/api/state").json()
    assert state["stats"]["released_cents"] == 6_000
    assert state["stats"]["payouts_cents"] == 6_000
    assert state["stats"]["blocked_attempts"] == 1
    assert state["stats"]["chain_ok"] is True
    active_job = next(item for item in state["jobs"] if item["id"] == job_id)
    assert active_job["status"] == "completed"
    assert all(
        milestone["status"] == "released" for milestone in active_job["milestones"]
    )

    payments = client.get("/api/state").json()["payments"]
    kinds = [payment["kind"] for payment in payments]
    assert kinds.count("capture") == 3
    assert kinds.count("payout") == 3
    assert kinds.count("authorization") == 1

    chain = client.get("/api/audit/verify").json()
    assert chain["ok"] is True
    assert chain["count"] >= 15


def test_spend_simulator_records_the_decision(client):
    preview = client.post(
        "/api/command", json={"text": DEMO_PROMPT, "post": False}
    ).json()
    mandate_id = preview["mandate"]["id"]

    blocked = client.post(
        "/api/spend/check",
        json={"mandate_id": mandate_id, "amount_cents": 7_500, "category": "design"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["verdict"] == "block"
    assert "budget_exceeded" in blocked.json()["reason_codes"]


def test_rejecting_an_approval_marks_the_job_rejected(client):
    preview = client.post(
        "/api/command", json={"text": DEMO_PROMPT, "post": False}
    ).json()
    mandate_id = preview["mandate"]["id"]
    plan = preview["plan"]
    total = sum(item["amount_cents"] for item in plan["milestones"])
    job = client.post(
        "/api/jobs",
        json={
            "mandate_id": mandate_id,
            "title": plan["title"],
            "category": "design",
            "amount_cents": total,
            "plan": plan,
        },
    ).json()["job"]

    waiting = client.post(f"/api/jobs/{job['id']}/authorize", json={}).json()
    client.post(
        f"/api/approvals/{waiting['approval']['id']}/resolve",
        json={"approve": False, "note": "Not this month."},
    )

    state = client.get("/api/state").json()
    updated = next(item for item in state["jobs"] if item["id"] == job["id"])
    assert updated["status"] == "rejected"
    assert updated["milestones"][0]["status"] == "pending"
