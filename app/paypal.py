"""PayPal Orders v2 + Payouts.

Real mode uses the REST API.  Mock mode implements the same state machine in
SQLite so the demo is deterministic and never needs credentials.

The wallet flow is:

1. ``POST /v2/checkout/orders`` with ``intent=AUTHORIZE`` for the job total.
2. ``POST /v2/checkout/orders/{id}/authorize`` -- funds are held, not captured.
3. ``POST /v2/payments/authorizations/{id}/capture`` once per approved
   milestone, with ``final_capture=true`` on the last one.
4. ``POST /v1/payments/payouts`` to send the freelancer their share.

Docs:
* Orders:   https://developer.paypal.com/docs/api/orders/v2/
* Payments: https://developer.paypal.com/docs/api/payments/v2/
* Payouts:  https://developer.paypal.com/docs/api/payments.payouts-batch/v1/
* Authorizations are valid for 29 days; see docs/PAYPAL.md for the caveats.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx

from .config import Settings
from .money import amount_value
from .store import Store
from .util import now_iso


class PayPalError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


def _mock_id(prefix: str) -> str:
    return f"MOCK-{prefix}-{secrets.token_hex(5).upper()}"


def _links(payload: dict) -> dict[str, str]:
    return {
        str(link.get("rel")): str(link.get("href"))
        for link in payload.get("links", [])
        if link.get("rel") and link.get("href")
    }


class PayPalClient:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def mode(self) -> str:
        return self.settings.paypal_environment

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    # -- transport --------------------------------------------------------
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.settings.paypal_base_url}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret),
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise PayPalError(
                "Could not obtain a PayPal access token. Check PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET.",
                status=response.status_code,
                details=response.text[:2000],
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        request_id: str | None = None,
        prefer: str | None = None,
    ) -> dict:
        if self.mode == "live" and not self.settings.allow_live:
            raise PayPalError("Live PayPal is disabled. Set ALLOW_LIVE=1 if you really mean it.")
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.request(
                method,
                f"{self.settings.paypal_base_url}{path}",
                json=json,
                headers=headers,
            )
        if response.status_code == 401:
            self._token = None
            return await self._request(method, path, json=json, request_id=request_id, prefer=prefer)
        if response.status_code >= 400:
            try:
                details = response.json()
                message = details.get("message") or details.get("name") or response.text
            except ValueError:
                details = response.text[:2000]
                message = response.text[:300]
            raise PayPalError(f"PayPal API error: {message}", status=response.status_code, details=details)
        if not response.content:
            return {}
        return response.json()

    # -- orders -----------------------------------------------------------
    async def create_order(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        custom_id: str,
        idempotency_key: str,
    ) -> dict:
        if self.is_mock:
            order_id = _mock_id("ORD")
            raw = {
                "id": order_id,
                "status": "CREATED",
                "intent": "AUTHORIZE",
                "purchase_units": [
                    {
                        "custom_id": custom_id,
                        "description": description[:127],
                        "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                    }
                ],
                "mock": True,
            }
            self.store.put_paypal_object(
                {
                    "id": order_id,
                    "kind": "order",
                    "status": "CREATED",
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "meta": {"description": description, "custom_id": custom_id},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
            return {"id": order_id, "status": "CREATED", "raw": raw, "links": {}}

        raw = await self._request(
            "POST",
            "/v2/checkout/orders",
            json={
                "intent": "AUTHORIZE",
                "purchase_units": [
                    {
                        "custom_id": custom_id,
                        "description": description[:127],
                        "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                    }
                ],
                "application_context": {
                    "brand_name": self.settings.app_name,
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                },
            },
            request_id=idempotency_key,
            prefer="return=representation",
        )
        return {"id": raw["id"], "status": raw.get("status", "CREATED"), "raw": raw, "links": _links(raw)}

    async def authorize_order(self, *, order_id: str, idempotency_key: str) -> dict:
        if self.is_mock:
            order = self.store.get_paypal_object(order_id)
            if not order:
                raise PayPalError(f"Mock order {order_id} not found")
            if order["status"] != "CREATED":
                raise PayPalError(f"Mock order {order_id} is {order['status']}, cannot authorize")
            auth_id = _mock_id("AUTH")
            auth = {
                "id": auth_id,
                "kind": "authorization",
                "status": "AUTHORIZED",
                "amount_cents": int(order["amount_cents"]),
                "currency": order["currency"],
                "meta": {"order_id": order_id, "captured_cents": 0},
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            self.store.put_paypal_object(auth)
            self.store.update_paypal_object(order_id, {"status": "AUTHORIZED", "meta": {**order["meta"], "authorization_id": auth_id}})
            raw = {
                "id": auth_id,
                "status": "AUTHORIZED",
                "amount": {"currency_code": order["currency"], "value": amount_value(int(order["amount_cents"]))},
                "supplementary_data": {"related_ids": {"order_id": order_id}},
                "mock": True,
            }
            return {"id": auth_id, "status": "AUTHORIZED", "order_id": order_id, "raw": raw, "links": {}}

        raw = await self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/authorize",
            json={},
            request_id=idempotency_key,
            prefer="return=representation",
        )
        authorization = (
            raw.get("purchase_units", [{}])[0].get("payments", {}).get("authorizations", [{}])[0]
        )
        auth_id = authorization.get("id")
        if not auth_id:
            raise PayPalError("PayPal did not return an authorization id", details=raw)
        return {
            "id": auth_id,
            "status": authorization.get("status", "AUTHORIZED"),
            "order_id": order_id,
            "raw": raw,
            "links": _links(raw),
        }

    async def capture_authorization(
        self,
        *,
        auth_id: str,
        amount_cents: int,
        currency: str,
        final: bool,
        idempotency_key: str,
    ) -> dict:
        if self.is_mock:
            auth = self.store.get_paypal_object(auth_id)
            if not auth or auth["kind"] != "authorization":
                raise PayPalError(f"Mock authorization {auth_id} not found")
            if auth["status"] not in {"AUTHORIZED", "PARTIALLY_CAPTURED"}:
                raise PayPalError(f"Mock authorization {auth_id} is {auth['status']}")
            remaining = int(auth["amount_cents"]) - int(auth["meta"].get("captured_cents", 0))
            if amount_cents > remaining:
                raise PayPalError(f"Capture of {amount_cents} exceeds remaining {remaining}")
            captured_total = int(auth["meta"].get("captured_cents", 0)) + amount_cents
            new_remaining = int(auth["amount_cents"]) - captured_total
            new_status = "CAPTURED" if (final or new_remaining <= 0) else "PARTIALLY_CAPTURED"
            capture_id = _mock_id("CAP")
            receipt = {
                "id": capture_id,
                "status": "COMPLETED",
                "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                "final_capture": final,
                "supplementary_data": {"related_ids": {"authorization_id": auth_id}},
                "mock": True,
            }
            self.store.put_paypal_object(
                {
                    "id": capture_id,
                    "kind": "capture",
                    "status": "COMPLETED",
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "meta": {"authorization_id": auth_id, "final": final},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
            self.store.update_paypal_object(
                auth_id,
                {"status": new_status, "meta": {**auth["meta"], "captured_cents": captured_total}},
            )
            return {"id": capture_id, "status": "COMPLETED", "raw": receipt, "links": {}}

        raw = await self._request(
            "POST",
            f"/v2/payments/authorizations/{auth_id}/capture",
            json={
                "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                "final_capture": final,
            },
            request_id=idempotency_key,
            prefer="return=representation",
        )
        return {
            "id": raw.get("id", ""),
            "status": raw.get("status", "COMPLETED"),
            "raw": raw,
            "links": _links(raw),
        }

    async def void_authorization(self, *, auth_id: str, idempotency_key: str) -> dict:
        if self.is_mock:
            auth = self.store.get_paypal_object(auth_id)
            if not auth:
                raise PayPalError(f"Mock authorization {auth_id} not found")
            self.store.update_paypal_object(auth_id, {"status": "VOIDED"})
            return {"id": auth_id, "status": "VOIDED", "raw": {"id": auth_id, "status": "VOIDED"}, "links": {}}

        raw = await self._request(
            "POST",
            f"/v2/payments/authorizations/{auth_id}/void",
            json={},
            request_id=idempotency_key,
            prefer="return=representation",
        )
        return {"id": raw.get("id", auth_id), "status": raw.get("status", "VOIDED"), "raw": raw, "links": _links(raw)}

    # -- payouts ----------------------------------------------------------
    async def create_payout(
        self,
        *,
        receiver_email: str,
        amount_cents: int,
        currency: str,
        note: str,
        sender_item_id: str,
        idempotency_key: str,
    ) -> dict:
        if self.is_mock:
            payout_id = _mock_id("PAYOUT")
            raw = {
                "batch_header": {
                    "payout_batch_id": payout_id,
                    "batch_status": "SUCCESS",
                    "sender_batch_id": idempotency_key,
                },
                "items": [
                    {
                        "payout_item_id": _mock_id("ITEM"),
                        "transaction_status": "SUCCESS",
                        "payout_item": {
                            "receiver": receiver_email,
                            "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                            "note": note,
                            "sender_item_id": sender_item_id,
                        },
                    }
                ],
                "mock": True,
            }
            self.store.put_paypal_object(
                {
                    "id": payout_id,
                    "kind": "payout",
                    "status": "SUCCESS",
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "meta": {"receiver": receiver_email, "note": note},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
            return {"id": payout_id, "status": "SUCCESS", "raw": raw, "links": {}}

        if not self.settings.paypal_payouts_enabled:
            raise PayPalError("PayPal Payouts are disabled (PAYPAL_PAYOUTS_ENABLED=0)")
        raw = await self._request(
            "POST",
            "/v1/payments/payouts",
            json={
                "sender_batch_header": {
                    "sender_batch_id": idempotency_key,
                    "email_subject": "You have a payment from MandatePay",
                    "email_message": note[:1000],
                },
                "items": [
                    {
                        "recipient_type": "EMAIL",
                        "amount": {"currency_code": currency, "value": amount_value(amount_cents)},
                        "receiver": receiver_email,
                        "note": note[:255],
                        "sender_item_id": sender_item_id,
                    }
                ],
            },
            request_id=idempotency_key,
            prefer="return=representation",
        )
        header = raw.get("batch_header", {})
        return {
            "id": header.get("payout_batch_id", ""),
            "status": header.get("batch_status", "PENDING"),
            "raw": raw,
            "links": _links(raw),
        }

    # -- webhooks ---------------------------------------------------------
    async def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        if self.is_mock:
            return True
        if not self.settings.paypal_webhook_id:
            return False
        payload = {
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "webhook_id": self.settings.paypal_webhook_id,
            "webhook_event": event,
        }
        raw = await self._request(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            json=payload,
        )
        return raw.get("verification_status") == "SUCCESS"
