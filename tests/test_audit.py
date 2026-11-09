"""The audit log is an append-only SHA-256 hash chain."""

from __future__ import annotations


def test_audit_chain_verifies_and_detects_tampering(service):
    initial = service.store.audit_verify()
    assert initial["ok"] is True
    assert initial["count"] == 1  # app.started

    service.store.append_audit(
        actor="agent",
        action="test.entry",
        decision="info",
        message="A test row for the hash chain.",
    )
    healthy = service.store.audit_verify()
    assert healthy["ok"] is True
    assert healthy["count"] == 2

    tampered = service.store.audit_tamper()
    assert tampered is not None
    broken = service.store.audit_verify()
    assert broken["ok"] is False
    assert broken["broken_at"] == tampered["seq"]


def test_reset_restarts_the_chain(service):
    service.store.append_audit(
        actor="agent", action="test.entry", decision="info", message="noise"
    )
    assert service.store.audit_verify()["count"] == 2

    state = service.reset_demo()

    assert state["stats"]["audit_entries"] == 1
    reset = service.store.audit_verify()
    assert reset["ok"] is True
    assert reset["count"] == 1


def test_tamper_endpoint_reports_the_broken_row(client):
    started = client.get("/api/health").json()
    assert started["audit_ok"] is True

    tampered = client.post("/api/audit/tamper")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["tampered_seq"] is not None
    assert body["verification"]["ok"] is False
    assert body["verification"]["broken_at"] == body["tampered_seq"]

    verified = client.get("/api/audit/verify").json()
    assert verified["ok"] is False
