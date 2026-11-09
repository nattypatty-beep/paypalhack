"""The mandate engine: every spend is checked against the principal's rules.

Design notes
------------
* Amounts are integer cents; no float arithmetic touches money.
* Hard blocks (budget, category, expiry, per-transaction cap) cannot be
  overridden by an approval.  Approvals only unlock the "above threshold" case.
* An approval is bound to one mandate, job, amount and category, is single-use,
  and expires.  It is a capability, not a blank cheque.
* Captures draw down an already-authorized job, so they do not re-trigger the
  approval threshold.  They still enforce budget, category, per-transaction and
  authorization-remaining limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import Verdict
from .store import Store
from .util import is_past, now_iso


@dataclass
class SpendRequest:
    mandate_id: str
    amount_cents: int
    currency: str = "USD"
    category: str = ""
    kind: str = "authorization"  # authorization | capture | simulation
    job_id: str | None = None
    milestone_id: str | None = None
    description: str = ""
    approval_id: str | None = None
    authorization_id: str | None = None


@dataclass
class Decision:
    verdict: str
    reasons: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict == Verdict.ALLOW.value

    @property
    def blocked(self) -> bool:
        return self.verdict == Verdict.BLOCK.value

    @property
    def needs_approval(self) -> bool:
        return self.verdict == Verdict.REQUIRE_APPROVAL.value

    @property
    def reason_codes(self) -> list[str]:
        return [reason["code"] for reason in self.reasons]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "reason_codes": self.reason_codes,
            "snapshot": self.snapshot,
            "approval": self.approval,
        }


def _reason(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


class MandateEngine:
    def __init__(self, store: Store):
        self.store = store

    def evaluate(self, request: SpendRequest) -> Decision:
        mandate = self.store.get_mandate(request.mandate_id)
        if not mandate:
            return Decision(
                verdict=Verdict.BLOCK.value,
                reasons=[_reason("mandate_not_found", "The mandate does not exist.")],
                snapshot={"request": request.__dict__, "checked_at": now_iso()},
            )

        financials = self.store.mandate_financials(mandate["id"])
        snapshot: dict[str, Any] = {
            "checked_at": now_iso(),
            "request": {
                "amount_cents": request.amount_cents,
                "currency": request.currency,
                "category": request.category,
                "kind": request.kind,
                "job_id": request.job_id,
                "milestone_id": request.milestone_id,
                "description": request.description,
                "approval_id": request.approval_id,
            },
            "mandate": {
                "id": mandate["id"],
                "budget_cents": mandate["budget_cents"],
                "approval_threshold_cents": mandate["approval_threshold_cents"],
                "per_txn_limit_cents": mandate["per_txn_limit_cents"],
                "daily_limit_cents": mandate["daily_limit_cents"],
                "categories": mandate["categories"],
                "status": mandate["status"],
                "expires_at": mandate["expires_at"],
            },
            "financials": financials,
        }

        reasons: list[dict[str, Any]] = []

        def block(code: str, message: str, **detail: Any) -> None:
            reasons.append(_reason(code, message, **detail))

        if mandate["status"] != "active":
            block("mandate_inactive", f"Mandate is {mandate['status']}.")
        if is_past(mandate.get("expires_at")):
            block("mandate_expired", "The mandate has expired.", expires_at=mandate.get("expires_at"))
        if request.amount_cents <= 0:
            block("invalid_amount", "Amount must be greater than zero.")
        if request.currency.upper() != str(mandate["currency"]).upper():
            block(
                "currency_mismatch",
                f"Spend is in {request.currency}, mandate is in {mandate['currency']}.",
            )

        categories = [str(c).lower() for c in mandate["categories"]]
        requested_category = (request.category or "").lower()
        if categories and "any" not in categories and requested_category not in categories:
            block(
                "category_not_allowed",
                f"Category '{request.category}' is outside the mandate.",
                allowed=categories,
            )

        per_txn = mandate.get("per_txn_limit_cents")
        if per_txn is not None and request.amount_cents > int(per_txn):
            block(
                "per_transaction_limit",
                f"Amount exceeds the per-transaction limit of {per_txn} cents.",
                limit_cents=per_txn,
            )

        daily = mandate.get("daily_limit_cents")
        if request.kind == "authorization" and daily:
            recent = self.store.recent_authorized_cents(mandate["id"])
            if recent + request.amount_cents > int(daily):
                block(
                    "daily_limit",
                    f"Daily limit of {daily} cents would be exceeded.",
                    already_cents=recent,
                    limit_cents=daily,
                )

        needs_approval = False
        approved_ok = False

        if request.kind in {"authorization", "simulation"}:
            projected = (
                financials["spent_cents"]
                + financials["committed_cents"]
                + financials["pending_cents"]
                + request.amount_cents
            )
            snapshot["projected_cents"] = projected
            snapshot["projected_after_cents"] = max(0, financials["budget_cents"] - projected)
            if projected > financials["budget_cents"]:
                block(
                    "budget_exceeded",
                    "This spend would exceed the mandate budget.",
                    budget_cents=financials["budget_cents"],
                    spent_cents=financials["spent_cents"],
                    committed_cents=financials["committed_cents"],
                    pending_cents=financials["pending_cents"],
                    projected_cents=projected,
                    over_by_cents=projected - financials["budget_cents"],
                )
            threshold = int(mandate["approval_threshold_cents"])
            # "ask me before anything over $40" means strictly greater than 40.
            if request.amount_cents > threshold:
                needs_approval = True

        elif request.kind == "capture":
            job_id = request.job_id
            if not job_id:
                block("job_required", "A capture must reference a job.")
            else:
                job = self.store.get_job(job_id, with_milestones=False)
                if not job:
                    block("job_not_found", "The job does not exist.")
                elif job["mandate_id"] != mandate["id"]:
                    block("job_mandate_mismatch", "The job belongs to a different mandate.")
                else:
                    if requested_category and job["category"].lower() != requested_category:
                        block(
                            "category_mismatch",
                            "Capture category does not match the job.",
                            job_category=job["category"],
                        )
                    job_fin = self.store.job_financials(job_id)
                    snapshot["job_financials"] = {
                        k: v for k, v in job_fin.items() if k != "authorization"
                    }
                    if not job_fin["authorization_id"]:
                        block("authorization_missing", "The job has no active PayPal authorization.")
                    elif request.amount_cents > job_fin["remaining_cents"]:
                        block(
                            "authorization_exhausted",
                            "Capture exceeds the remaining authorized amount.",
                            remaining_cents=job_fin["remaining_cents"],
                        )
                    if financials["spent_cents"] + financials["committed_cents"] > financials["budget_cents"]:
                        block("budget_exceeded", "The mandate is already over budget.")

        # An approval is only useful for the above-threshold case, and it can
        # never override a hard block.
        if request.approval_id:
            approval = self.store.get_approval(request.approval_id)
            snapshot["approval"] = (
                {
                    "id": approval["id"],
                    "status": approval["status"],
                    "amount_cents": approval["amount_cents"],
                    "job_id": approval["job_id"],
                    "category": approval["category"],
                    "token_expires_at": approval.get("token_expires_at"),
                    "used_at": approval.get("used_at"),
                }
                if approval
                else None
            )
            if not approval:
                block("approval_not_found", "The referenced approval does not exist.")
            elif approval["status"] == "pending":
                needs_approval = True
                snapshot["pending_approval_id"] = approval["id"]
            elif approval["status"] != "approved":
                block(
                    f"approval_{approval['status']}",
                    f"The approval is {approval['status']}, not approved.",
                )
            elif approval.get("used_at"):
                block("approval_used", "This approval has already been used.")
            elif is_past(approval.get("token_expires_at")):
                block("approval_expired", "The approval token has expired.")
            elif int(approval["amount_cents"]) != request.amount_cents:
                block(
                    "approval_amount_mismatch",
                    "The approval is for a different amount.",
                    approval_cents=approval["amount_cents"],
                    requested_cents=request.amount_cents,
                )
            elif (approval.get("job_id") or "") != (request.job_id or ""):
                block("approval_job_mismatch", "The approval is for a different job.")
            elif (approval.get("category") or "").lower() != requested_category:
                block("approval_category_mismatch", "The approval is for a different category.")
            elif approval["mandate_id"] != mandate["id"]:
                block("approval_mandate_mismatch", "The approval belongs to a different mandate.")
            else:
                approved_ok = True

        if reasons:
            snapshot["verdict"] = Verdict.BLOCK.value
            return Decision(verdict=Verdict.BLOCK.value, reasons=reasons, snapshot=snapshot)

        if needs_approval and not approved_ok:
            reasons.append(
                _reason(
                    "approval_threshold",
                    "Spend is within budget but above the human approval threshold.",
                    threshold_cents=mandate["approval_threshold_cents"],
                    amount_cents=request.amount_cents,
                )
            )
            snapshot["verdict"] = Verdict.REQUIRE_APPROVAL.value
            return Decision(verdict=Verdict.REQUIRE_APPROVAL.value, reasons=reasons, snapshot=snapshot)

        snapshot["verdict"] = Verdict.ALLOW.value
        return Decision(verdict=Verdict.ALLOW.value, reasons=[], snapshot=snapshot)
