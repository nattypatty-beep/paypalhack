#!/usr/bin/env python3
"""Small end-to-end smoke test for a running MandatePay server.

It exercises the parts that must work in mock mode: mandate parsing, the hard
budget block, the audit chain and the demo reset. It writes nothing permanent
because it resets the demo before and after the checks.

Usage:
    python scripts/smoke.py
    python scripts/smoke.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import sys

import httpx

PROMPT = "Get me a logo for my bakery, max $60, ask me before anything over $40."


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' - ' + detail) if detail else ''}")
    if not condition:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running MandatePay server.")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Server base URL"
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=20.0) as client:
        health = client.get("/api/health")
        check("health endpoint", health.status_code == 200, f"HTTP {health.status_code}")
        body = health.json()
        check("mock PayPal mode", body.get("paypal_mode") == "mock", str(body.get("paypal_mode")))
        check("audit chain health", body.get("audit_ok") is True)

        reset = client.post("/api/demo/reset")
        check("demo reset", reset.status_code == 200, f"HTTP {reset.status_code}")

        preview = client.post("/api/command", json={"text": PROMPT, "post": False})
        check("mandate parsing", preview.status_code == 200, preview.text[:160])
        parsed = preview.json()
        mandate = parsed["mandate"]
        check("budget parsed", mandate["budget_cents"] == 6_000, str(mandate["budget_cents"]))
        check(
            "approval threshold parsed",
            mandate["approval_threshold_cents"] == 4_000,
            str(mandate["approval_threshold_cents"]),
        )
        plan_total = sum(item["amount_cents"] for item in parsed["plan"]["milestones"])
        check("plan sums to budget", plan_total == 6_000, str(plan_total))

        spend = client.post(
            "/api/spend/check",
            json={
                "mandate_id": mandate["id"],
                "amount_cents": 7_500,
                "category": "design",
            },
        )
        check("hard budget block", spend.status_code == 200, spend.text[:160])
        decision = spend.json()
        check("overspend verdict", decision["verdict"] == "block", decision["verdict"])
        check("budget reason code", "budget_exceeded" in decision["reason_codes"])

        chain = client.get("/api/audit/verify")
        check("audit chain verifies", chain.status_code == 200 and chain.json()["ok"] is True)

        client.post("/api/demo/reset")

    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
