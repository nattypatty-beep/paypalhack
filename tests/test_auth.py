"""Optional HTTP Basic auth for public deployments."""

from __future__ import annotations

import base64
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.support import make_settings


def _client(tmp_path, user: str = "judge", password: str = "correct horse") -> TestClient:
    settings = replace(
        make_settings(tmp_path), basic_auth_user=user, basic_auth_password=password
    )
    return TestClient(create_app(settings))


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_auth_is_off_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/health").json()["auth_enabled"] is False


def test_auth_blocks_dashboard_api_and_docs(tmp_path):
    with _client(tmp_path) as client:
        for path in ("/", "/api/state", "/docs", "/openapi.json", "/static/app.js"):
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"].startswith("Basic ")
            assert response.json() == {"error": "Authentication required."}


def test_health_and_webhook_stay_public(tmp_path):
    with _client(tmp_path) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["auth_enabled"] is True
        webhook = client.post("/api/webhooks/paypal", json={"event_type": "TEST"})
        assert webhook.status_code != 401


def test_correct_credentials_are_allowed(tmp_path):
    with _client(tmp_path) as client:
        headers = _basic("judge", "correct horse")
        assert client.get("/", headers=headers).status_code == 200
        assert client.get("/api/state", headers=headers).status_code == 200
        assert client.get("/api/audit/verify", headers=headers).status_code == 200


def test_wrong_or_malformed_credentials_are_rejected(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/state", headers=_basic("judge", "wrong")).status_code == 401
        assert client.get("/api/state", headers=_basic("other", "correct horse")).status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Basic not-base64!"}).status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Bearer token"}).status_code == 401


def test_mock_webhook_is_untrusted_without_a_webhook_id(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.post(
            "/api/webhooks/paypal",
            json={"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "PAY-1"}},
        )
        assert response.status_code == 200
        assert response.json() == {"received": True, "verified": False}
        entries = client.get("/api/audit").json()["entries"]
        webhook_rows = [row for row in entries if row["action"] == "webhook.received"]
        assert webhook_rows and webhook_rows[-1]["decision"] == "error"


def test_mock_webhook_can_be_explicitly_trusted(tmp_path):
    settings = replace(make_settings(tmp_path), paypal_webhook_id="WH-DEMO")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/webhooks/paypal", json={"event_type": "TEST"})
        assert response.status_code == 200
        assert response.json()["verified"] is True


def test_partial_configuration_does_not_enable_auth(tmp_path):
    settings = replace(
        make_settings(tmp_path), basic_auth_user="judge", basic_auth_password=""
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/health").json()["auth_enabled"] is False
