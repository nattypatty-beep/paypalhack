"""Application service: the high-level operations behind the API and the demo."""

from __future__ import annotations

import shutil
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .events import EventBus
from .inspect import inspect_path
from .llm import LLMClient
from .mandate import Decision, MandateEngine, SpendRequest
from .models import (
    ApprovalStatus,
    CreateJobRequest,
    Criterion,
    JobPlan,
    JobStatus,
    MilestoneStatus,
    ResolveApprovalRequest,
    SpendCheckRequest,
    SubmitWorkRequest,
)
from .money import fmt_cents
from .nlp import parse_mandate
from .paypal import PayPalClient, PayPalError
from .planner import Planner, normalize_category
from .store import Database, Store
from .util import is_past, new_id, now, now_iso, seconds_until
from .verify import Verifier

ALLOWED_UPLOAD_EXTENSIONS = {
    "svg",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "pdf",
    "md",
    "txt",
    "csv",
    "json",
    "zip",
    "mp3",
    "wav",
    "flac",
    "mp4",
    "mov",
    "webm",
    "docx",
    "doc",
}

DEMO_NOTES = {
    "concept": (
        "Three directions for the bakery identity. 1) A hearth-and-steam mark with a warm serif "
        "wordmark. 2) An artisan bread loaf built from three simple strokes. 3) A circular seal with "
        "a wheat motif. My recommendation is direction 2: it reads at small sizes, feels warm and "
        "hand-made, and gives the bakery a friendly, local voice."
    ),
    "draft": (
        "Early draft attached for the logo master. Placeholder layout with a quick colour test. "
        "The mark still needs a proper wordmark and the palette is not final yet."
    ),
    "final": (
        "Final logo master for the Hearth & Crumb bakery. The mark pairs a warm artisan loaf with a "
        "hand-drawn steam line and a confident wordmark. It uses a three-colour palette, has a "
        "transparent background, and is delivered as editable vector source so it scales from a "
        "shopfront sign to a social avatar without losing clarity."
    ),
    "brandkit": (
        "Final brand kit and handoff. Included are the vector master and a short usage guide. The "
        "work is delivered under a perpetual licence with full source files and commercial usage "
        "rights, so the bakery can use it on packaging, signage and digital channels."
    ),
}

DEMO_SAMPLES = {
    "concept": ["concept-board.svg"],
    "draft": ["logo-draft.svg"],
    "final": ["logo-final.svg"],
    "brandkit": ["logo-final.svg", "brand-guide.md"],
}


class ServiceError(ValueError):
    """A user-facing error."""


class AppService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.store = Store(self.db)
        self.bus = EventBus()
        self.llm = LLMClient(settings)
        self.planner = Planner(self.llm)
        self.verifier = Verifier(self.llm, settings)
        self.engine = MandateEngine(self.store)
        self.paypal = PayPalClient(settings, self.store)

    # -- infrastructure ---------------------------------------------------
    def bootstrap(self) -> None:
        if self.store.audit_count() == 0:
            self._audit(
                actor="system",
                action="app.started",
                decision="info",
                message="MandatePay started. Audit chain initialized.",
                meta={
                    "paypal_mode": self.paypal.mode,
                    "llm_provider": self.llm.provider,
                    "llm_enabled": self.llm.enabled,
                },
            )

    def _audit(self, **kwargs: Any) -> dict:
        entry = self.store.append_audit(**kwargs)
        return entry

    def _publish(self, kind: str = "state", **extra: Any) -> None:
        self.bus.publish(kind=kind, **extra)

    # -- state ------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        self.store.expire_stale_approvals()
        mandates = []
        for mandate in self.store.list_mandates():
            financials = self.store.mandate_financials(mandate["id"])
            mandates.append({**mandate, "financials": financials})

        jobs = self.store.list_jobs()
        for job in jobs:
            job["financials"] = self.store.job_financials(job["id"])
        approvals = self.store.list_approvals()
        for approval in approvals:
            approval["seconds_left"] = seconds_until(approval.get("expires_at"))
        payments = self.store.list_payments(250)
        audit = self.store.list_audit(300)
        chain = self.store.audit_verify()

        active = mandates[0] if mandates else None
        financials = active["financials"] if active else {
            "budget_cents": 0,
            "spent_cents": 0,
            "committed_cents": 0,
            "pending_cents": 0,
            "available_cents": 0,
        }
        released = sum(
            int(payment["amount_cents"])
            for payment in payments
            if payment["kind"] == "capture" and payment["status"] == "captured"
        )
        payouts = sum(
            int(payment["amount_cents"])
            for payment in payments
            if payment["kind"] == "payout" and payment["status"] == "completed"
        )
        stats = {
            **financials,
            "blocked_attempts": self.store.count_blocked(),
            "audit_entries": chain.get("count", 0),
            "chain_ok": chain.get("ok", False),
            "broken_at": chain.get("broken_at"),
            "jobs_total": len(jobs),
            "jobs_completed": sum(1 for job in jobs if job["status"] == JobStatus.COMPLETED.value),
            "approvals_pending": sum(1 for item in approvals if item["status"] == "pending"),
            "released_cents": released,
            "payouts_cents": payouts,
            "spent_display": fmt_cents(financials.get("spent_cents", 0), active["currency"] if active else "USD"),
            "budget_display": fmt_cents(financials.get("budget_cents", 0), active["currency"] if active else "USD"),
        }
        return {
            "mandates": mandates,
            "active_mandate_id": active["id"] if active else None,
            "jobs": jobs,
            "approvals": approvals,
            "payments": payments,
            "audit": audit,
            "stats": stats,
            "config": {
                "app_name": self.settings.app_name,
                "demo_mode": self.settings.demo_mode,
                "paypal_mode": self.paypal.mode,
                "paypal_configured": self.settings.paypal_real,
                "paypal_env": self.settings.paypal_env,
                "paypal_payouts": self.settings.paypal_payouts_enabled,
                "llm_enabled": self.llm.enabled,
                "llm_provider": self.llm.provider,
                "llm_model": self.llm.model or None,
                "freelancer_name": self.settings.freelancer_name,
                "freelancer_email": self.settings.freelancer_email,
                "platform_fee_bps": self.settings.platform_fee_bps,
                "approval_ttl_minutes": self.settings.approval_ttl_minutes,
                "approval_token_ttl_minutes": self.settings.approval_token_ttl_minutes,
            },
            "version": self.bus.version,
            "server_time": now_iso(),
        }

    # -- mandates ---------------------------------------------------------
    async def command(self, text: str, principal: str = "you", post: bool = True) -> dict[str, Any]:
        try:
            parsed = await parse_mandate(text, principal, self.llm)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        budget = int(parsed["budget_cents"])
        threshold = int(parsed["approval_threshold_cents"])
        if budget <= 0:
            raise ServiceError("The budget must be greater than zero.")
        threshold = max(0, min(threshold, budget))
        mandate_id = new_id("mnd")
        expires = (now() + timedelta(days=int(parsed.get("expires_in_days") or 7))).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        mandate = self.store.insert_mandate(
            {
                "id": mandate_id,
                "principal": parsed.get("principal") or principal,
                "currency": parsed.get("currency") or "USD",
                "budget_cents": budget,
                "approval_threshold_cents": threshold,
                "per_txn_limit_cents": parsed.get("per_txn_limit_cents"),
                "daily_limit_cents": parsed.get("daily_limit_cents"),
                "categories": parsed.get("categories") or [],
                "status": "active",
                "raw_text": text,
                "created_at": now_iso(),
                "expires_at": expires,
            }
        )
        self._audit(
            actor="human",
            action="mandate.created",
            subject_type="mandate",
            subject_id=mandate_id,
            decision="allow",
            amount_cents=budget,
            currency=mandate["currency"],
            message=(
                f"Mandate: budget {fmt_cents(budget, mandate['currency'])}, "
                f"ask above {fmt_cents(threshold, mandate['currency'])}, "
                f"categories {', '.join(mandate['categories']) or 'any'}."
            ),
            meta={"raw_text": text, "parsed_by": parsed.get("source"), "confidence": parsed.get("confidence")},
        )
        plan = await self.planner.plan(
            brief=parsed.get("job_brief") or text,
            category=parsed.get("category") or "other",
            budget_cents=budget,
            title=parsed.get("job_title"),
        )
        self._audit(
            actor="agent",
            action="plan.drafted",
            subject_type="mandate",
            subject_id=mandate_id,
            decision="proposed",
            amount_cents=plan.total_cents,
            currency=mandate["currency"],
            message=f"Drafted {len(plan.milestones)} milestones with {sum(len(m.criteria) for m in plan.milestones)} acceptance criteria.",
            meta={"engine": plan.engine, "title": plan.title},
        )
        job: dict[str, Any] | None = None
        if post:
            created = await self.create_job(
                CreateJobRequest(
                    mandate_id=mandate_id,
                    title=plan.title,
                    brief=parsed.get("job_brief") or text,
                    category=parsed.get("category") or "other",
                    amount_cents=plan.total_cents,
                    currency=mandate["currency"],
                    plan=plan,
                )
            )
            job = created.get("job")
        self._publish("mandate")
        public = {**mandate, "financials": self.store.mandate_financials(mandate_id)}
        result: dict[str, Any] = {"mandate": public, "plan": plan.model_dump(), "parsed": parsed}
        if job:
            result["job"] = job
        return result

    async def create_mandate(self, request: Any) -> dict[str, Any]:
        """Create a mandate from explicit fields (used by the API and tests)."""
        budget = int(request.budget_cents)
        threshold = max(0, min(int(request.approval_threshold_cents), budget))
        mandate_id = new_id("mnd")
        expires = (now() + timedelta(days=int(request.expires_in_days))).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        mandate = self.store.insert_mandate(
            {
                "id": mandate_id,
                "principal": request.principal,
                "currency": (request.currency or "USD").upper(),
                "budget_cents": budget,
                "approval_threshold_cents": threshold,
                "per_txn_limit_cents": request.per_txn_limit_cents,
                "daily_limit_cents": request.daily_limit_cents,
                "categories": [normalize_category(c) if c else c for c in request.categories],
                "status": "active",
                "raw_text": request.raw_text,
                "created_at": now_iso(),
                "expires_at": expires,
            }
        )
        self._audit(
            actor="human",
            action="mandate.created",
            subject_type="mandate",
            subject_id=mandate_id,
            decision="allow",
            amount_cents=budget,
            currency=mandate["currency"],
            message=(
                f"Mandate: budget {fmt_cents(budget, mandate['currency'])}, "
                f"ask above {fmt_cents(threshold, mandate['currency'])}."
            ),
            meta={"raw_text": request.raw_text, "parsed_by": "explicit"},
        )
        category = normalize_category(request.categories[0]) if request.categories else "other"
        plan = await self.planner.plan(
            brief=request.raw_text or "Freelance project",
            category=category,
            budget_cents=budget,
        )
        self._publish("mandate")
        return {
            "mandate": {**mandate, "financials": self.store.mandate_financials(mandate_id)},
            "plan": plan.model_dump(),
        }

    # -- jobs -------------------------------------------------------------
    async def create_job(self, request: CreateJobRequest) -> dict[str, Any]:
        mandate = self.store.get_mandate(request.mandate_id)
        if not mandate:
            raise ServiceError("Mandate not found.")
        if mandate["status"] != "active":
            raise ServiceError(f"Mandate is {mandate['status']}.")
        category = normalize_category(request.category)
        currency = request.currency or mandate["currency"]
        amount = int(request.amount_cents)
        if amount <= 0:
            raise ServiceError("The job amount must be greater than zero.")

        plan: JobPlan
        if request.plan:
            plan = request.plan
            if plan.total_cents != amount:
                raise ServiceError(
                    f"Plan amounts sum to {plan.total_cents} cents but the job amount is {amount} cents."
                )
        else:
            plan = await self.planner.plan(
                brief=request.brief,
                category=category,
                budget_cents=amount,
                title=request.title,
            )

        job_id = new_id("job")
        title = request.title or plan.title
        job = self.store.insert_job(
            {
                "id": job_id,
                "mandate_id": mandate["id"],
                "title": title,
                "brief": request.brief or plan.summary,
                "category": category,
                "currency": currency,
                "total_cents": amount,
                "status": JobStatus.PLANNED.value,
                "freelancer_name": request.freelancer_name or self.settings.freelancer_name,
                "freelancer_email": request.freelancer_email or self.settings.freelancer_email,
                "paypal_order_id": None,
                "paypal_auth_id": None,
                "blocked_reason": None,
                "meta": {
                    "plan_engine": plan.engine,
                    "summary": plan.summary,
                    "rationale": plan.rationale,
                },
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        for index, milestone in enumerate(plan.milestones):
            due = (now() + timedelta(days=milestone.due_in_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
            self.store.insert_milestone(
                {
                    "id": new_id("ms"),
                    "job_id": job_id,
                    "idx": index,
                    "title": milestone.title,
                    "description": milestone.description,
                    "amount_cents": milestone.amount_cents,
                    "currency": currency,
                    "criteria": [criterion.model_dump() for criterion in milestone.criteria],
                    "status": MilestoneStatus.PENDING.value,
                    "due_at": due,
                    "submission_id": None,
                    "capture_id": None,
                    "payout_id": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
        job = self.store.get_job(job_id)
        assert job is not None
        self._audit(
            actor="agent",
            action="job.posted",
            subject_type="job",
            subject_id=job_id,
            decision="proposed",
            amount_cents=amount,
            currency=currency,
            message=f"Posted '{title}' with {len(plan.milestones)} AI-generated milestones.",
            meta={
                "plan_engine": plan.engine,
                "milestones": [
                    {"title": item.title, "amount_cents": item.amount_cents, "criteria": len(item.criteria)}
                    for item in plan.milestones
                ],
            },
        )
        self._publish("job")
        result: dict[str, Any] = {"job": job}
        if request.auto_authorize:
            result.update(await self.authorize_job(job_id))
        return result

    async def authorize_job(self, job_id: str, approval_id: str | None = None) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise ServiceError("Job not found.")
        if job["status"] in {
            JobStatus.AUTHORIZED.value,
            JobStatus.IN_PROGRESS.value,
            JobStatus.COMPLETED.value,
        }:
            return {"status": "authorized", "job": job, "already_authorized": True}

        # Idempotent retry: the job already has a live approval request. Return it
        # instead of counting the reserved amount against the budget a second time.
        if job["status"] == JobStatus.AWAITING_APPROVAL.value:
            pending = self.store.find_pending_approval(
                job["mandate_id"], job_id, int(job["total_cents"])
            )
            if pending:
                return {
                    "status": "approval_required",
                    "decision": {
                        "verdict": "require_approval",
                        "reasons": pending.get("reasons", []),
                        "reason_codes": ["approval_threshold"],
                        "snapshot": pending.get("snapshot", {}),
                        "approval": pending,
                    },
                    "approval": pending,
                    "job": job,
                }

        decision = self.engine.evaluate(
            SpendRequest(
                mandate_id=job["mandate_id"],
                amount_cents=int(job["total_cents"]),
                currency=job["currency"],
                category=job["category"],
                kind="authorization",
                job_id=job_id,
                description=f"Authorize escrow for '{job['title']}'",
                approval_id=approval_id,
            )
        )

        if decision.blocked:
            reason = "; ".join(decision.reason_codes)
            self.store.update_job(
                job_id, {"status": JobStatus.BLOCKED.value, "blocked_reason": reason}
            )
            self._audit(
                actor="agent",
                action="spend.blocked",
                subject_type="job",
                subject_id=job_id,
                decision="block",
                amount_cents=int(job["total_cents"]),
                currency=job["currency"],
                reason_codes=decision.reason_codes,
                message="Mandate blocked the authorization: " + ", ".join(decision.reason_codes),
                meta={"snapshot": decision.snapshot, "reasons": decision.reasons},
            )
            self._publish("blocked")
            return {
                "status": "blocked",
                "decision": decision.to_dict(),
                "job": self.store.get_job(job_id),
            }

        if decision.needs_approval:
            approval = self.store.find_pending_approval(
                job["mandate_id"], job_id, int(job["total_cents"])
            )
            if not approval:
                approval_id_new = new_id("apr")
                approval = self.store.insert_approval(
                    {
                        "id": approval_id_new,
                        "mandate_id": job["mandate_id"],
                        "job_id": job_id,
                        "milestone_id": None,
                        "amount_cents": int(job["total_cents"]),
                        "currency": job["currency"],
                        "category": job["category"],
                        "status": ApprovalStatus.PENDING.value,
                        "reasons": decision.reasons,
                        "snapshot": decision.snapshot,
                        "requested_at": now_iso(),
                        "expires_at": (
                            now() + timedelta(minutes=self.settings.approval_ttl_minutes)
                        ).isoformat(timespec="seconds").replace("+00:00", "Z"),
                        "resolved_at": None,
                        "resolver": None,
                        "note": "",
                        "token": None,
                        "token_expires_at": None,
                        "used_at": None,
                    }
                )
            self.store.update_job(job_id, {"status": JobStatus.AWAITING_APPROVAL.value})
            self._audit(
                actor="agent",
                action="spend.require_approval",
                subject_type="job",
                subject_id=job_id,
                decision="require_approval",
                amount_cents=int(job["total_cents"]),
                currency=job["currency"],
                reason_codes=["approval_threshold"],
                message=(
                    f"Authorization of {fmt_cents(int(job['total_cents']), job['currency'])} is above the "
                    f"human approval threshold; waiting for the principal."
                ),
                meta={"approval_id": approval["id"], "snapshot": decision.snapshot},
            )
            self._publish("approval")
            return {
                "status": "approval_required",
                "decision": decision.to_dict(),
                "approval": approval,
                "job": self.store.get_job(job_id),
            }

        # Allowed: create the PayPal order and authorize it.
        try:
            order = await self.paypal.create_order(
                amount_cents=int(job["total_cents"]),
                currency=job["currency"],
                description=f"MandatePay escrow: {job['title']}",
                custom_id=job_id,
                idempotency_key=f"order-{job_id}",
            )
            authorization = await self.paypal.authorize_order(
                order_id=order["id"],
                idempotency_key=f"auth-{job_id}",
            )
        except PayPalError as exc:
            self.store.update_job(
                job_id,
                {"status": JobStatus.PAYMENT_ERROR.value, "blocked_reason": str(exc)},
            )
            self._audit(
                actor="paypal",
                action="paypal.error",
                subject_type="job",
                subject_id=job_id,
                decision="error",
                amount_cents=int(job["total_cents"]),
                currency=job["currency"],
                message=str(exc),
                meta={"details": exc.details, "status": exc.status},
            )
            self._publish("error")
            return {"status": "payment_error", "error": str(exc), "job": self.store.get_job(job_id)}

        payment = self.store.insert_payment(
            {
                "id": new_id("pay"),
                "kind": "authorization",
                "status": "authorized",
                "job_id": job_id,
                "milestone_id": None,
                "paypal_id": authorization["id"],
                "related_id": order["id"],
                "amount_cents": int(job["total_cents"]),
                "fee_cents": 0,
                "currency": job["currency"],
                "description": f"Authorization for {job['title']}",
                "receipt": {"order": order["raw"], "authorization": authorization["raw"]},
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        self.store.update_job(
            job_id,
            {
                "status": JobStatus.AUTHORIZED.value,
                "paypal_order_id": order["id"],
                "paypal_auth_id": authorization["id"],
                "blocked_reason": None,
            },
        )
        if approval_id:
            approval = self.store.get_approval(approval_id)
            if approval and approval["status"] == ApprovalStatus.APPROVED.value:
                self.store.update_approval(
                    approval_id,
                    {"status": ApprovalStatus.CONSUMED.value, "used_at": now_iso()},
                )
        self._audit(
            actor="agent",
            action="spend.authorized",
            subject_type="job",
            subject_id=job_id,
            decision="allow",
            amount_cents=int(job["total_cents"]),
            currency=job["currency"],
            message=(
                f"Authorized {fmt_cents(int(job['total_cents']), job['currency'])}. "
                "Funds are held, not released."
            ),
            meta={
                "paypal_mode": self.paypal.mode,
                "order_id": order["id"],
                "authorization_id": authorization["id"],
                "approval_id": approval_id,
                "payment_id": payment["id"],
            },
        )
        self._publish("authorized")
        return {
            "status": "authorized",
            "job": self.store.get_job(job_id),
            "payment": payment,
        }

    # -- approvals --------------------------------------------------------
    def resolve_approval(self, approval_id: str, request: ResolveApprovalRequest, resolver: str = "you") -> dict:
        approval = self.store.get_approval(approval_id)
        if not approval:
            raise ServiceError("Approval not found.")
        if approval["status"] != ApprovalStatus.PENDING.value:
            raise ServiceError(f"Approval is already {approval['status']}.")
        if is_past(approval.get("expires_at")):
            self.store.update_approval(
                approval_id, {"status": ApprovalStatus.EXPIRED.value, "resolved_at": now_iso()}
            )
            raise ServiceError("This approval request has expired.")

        if request.approve:
            updates = {
                "status": ApprovalStatus.APPROVED.value,
                "resolved_at": now_iso(),
                "resolver": resolver,
                "note": request.note,
                "token": secrets.token_urlsafe(24),
                "token_expires_at": (
                    now() + timedelta(minutes=self.settings.approval_token_ttl_minutes)
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        else:
            updates = {
                "status": ApprovalStatus.REJECTED.value,
                "resolved_at": now_iso(),
                "resolver": resolver,
                "note": request.note,
            }
        self.store.update_approval(approval_id, updates)
        approval = self.store.get_approval(approval_id)
        assert approval is not None
        if not request.approve and approval.get("job_id"):
            job = self.store.get_job(approval["job_id"], with_milestones=False)
            if job and job["status"] == JobStatus.AWAITING_APPROVAL.value:
                self.store.update_job(job["id"], {"status": JobStatus.REJECTED.value})
        self._audit(
            actor="human",
            action="approval.approved" if request.approve else "approval.rejected",
            subject_type="approval",
            subject_id=approval_id,
            decision="allow" if request.approve else "block",
            amount_cents=int(approval["amount_cents"]),
            currency=approval["currency"],
            message=(
                f"Principal {'approved' if request.approve else 'rejected'} "
                f"{fmt_cents(int(approval['amount_cents']), approval['currency'])} for "
                f"{approval.get('job_id') or 'a spend'}."
            ),
            meta={"note": request.note, "resolver": resolver, "job_id": approval.get("job_id")},
        )
        self._publish("approval_resolved")
        return approval

    # -- spend simulation -------------------------------------------------
    def check_spend(self, request: SpendCheckRequest) -> dict:
        decision = self.engine.evaluate(
            SpendRequest(
                mandate_id=request.mandate_id,
                amount_cents=int(request.amount_cents),
                category=request.category,
                kind="simulation",
                description=request.description,
            )
        )
        if request.record:
            self._audit(
                actor="agent",
                action="spend.simulated",
                subject_type="mandate",
                subject_id=request.mandate_id,
                decision=decision.verdict,
                amount_cents=int(request.amount_cents),
                currency=(self.store.get_mandate(request.mandate_id) or {}).get("currency", "USD"),
                reason_codes=decision.reason_codes,
                message=request.description,
                meta={"reasons": decision.reasons, "snapshot": decision.snapshot},
            )
            self._publish("spend_check")
        return decision.to_dict()

    # -- submissions ------------------------------------------------------
    def _resolve_deliverables(self, request: SubmitWorkRequest) -> list[dict]:
        deliverables: list[dict] = []
        for name in request.samples:
            path = (self.settings.samples_dir / name).resolve()
            if self.settings.samples_dir.resolve() not in path.parents or not path.is_file():
                raise ServiceError(f"Sample file not found: {name}")
            inspection = inspect_path(path, name)
            deliverables.append(
                {
                    "id": new_id("del"),
                    "name": name,
                    "path": str(path),
                    "url": f"/samples/{name}",
                    "mime": inspection.get("mime", "application/octet-stream"),
                    "size": inspection.get("size", 0),
                    "sha256": inspection.get("sha256", ""),
                    "inspection": inspection,
                }
            )
        for upload_id in request.upload_ids:
            upload = self.store.get_upload(upload_id)
            if not upload:
                raise ServiceError(f"Upload not found: {upload_id}")
            path = Path(upload["path"])
            if not path.is_file():
                raise ServiceError(f"Uploaded file missing: {upload['name']}")
            inspection = inspect_path(path, upload["name"])
            deliverables.append(
                {
                    "id": upload_id,
                    "name": upload["name"],
                    "path": str(path),
                    "url": f"/api/uploads/{upload_id}/content",
                    "mime": upload.get("mime") or inspection.get("mime", "application/octet-stream"),
                    "size": upload.get("size") or inspection.get("size", 0),
                    "sha256": upload.get("sha256") or inspection.get("sha256", ""),
                    "inspection": inspection,
                }
            )
        return deliverables

    def submit_work(
        self,
        milestone_id: str,
        request: SubmitWorkRequest,
        *,
        actor: str = "freelancer",
        samples: list[str] | None = None,
    ) -> dict:
        milestone = self.store.get_milestone(milestone_id)
        if not milestone:
            raise ServiceError("Milestone not found.")
        job = self.store.get_job(milestone["job_id"])
        if not job:
            raise ServiceError("Job not found.")
        if job["status"] not in {
            JobStatus.AUTHORIZED.value,
            JobStatus.IN_PROGRESS.value,
            JobStatus.COMPLETED.value,
        }:
            raise ServiceError("The job is not authorized yet.")
        if samples:
            request = request.model_copy(update={"samples": samples})
        deliverables = self._resolve_deliverables(request)
        if not deliverables:
            raise ServiceError("Attach at least one file (or use the demo samples).")

        submission_id = new_id("sub")
        submission = self.store.insert_submission(
            {
                "id": submission_id,
                "milestone_id": milestone_id,
                "job_id": job["id"],
                "notes": request.notes,
                "deliverables": deliverables,
                "status": "received",
                "verdict": None,
                "created_at": now_iso(),
            }
        )
        self.store.update_milestone(
            milestone_id,
            {"status": MilestoneStatus.SUBMITTED.value, "submission_id": submission_id},
        )
        if job["status"] == JobStatus.AUTHORIZED.value:
            self.store.update_job(job["id"], {"status": JobStatus.IN_PROGRESS.value})
        self._audit(
            actor=actor,
            action="work.submitted",
            subject_type="milestone",
            subject_id=milestone_id,
            decision="submitted",
            amount_cents=int(milestone["amount_cents"]),
            currency=milestone["currency"],
            message=(
                f"Submitted {len(deliverables)} file(s) for '{milestone['title']}': "
                + ", ".join(str(item["name"]) for item in deliverables)
            ),
            meta={
                "submission_id": submission_id,
                "files": [item["name"] for item in deliverables],
                "notes_words": len((request.notes or "").split()),
            },
        )
        self._publish("submitted")
        return submission

    # -- verification & settlement ---------------------------------------
    async def verify_submission(self, submission_id: str) -> dict:
        submission = self.store.get_submission(submission_id)
        if not submission:
            raise ServiceError("Submission not found.")
        milestone = self.store.get_milestone(submission["milestone_id"])
        if not milestone:
            raise ServiceError("Milestone not found.")
        job = self.store.get_job(milestone["job_id"])
        if not job:
            raise ServiceError("Job not found.")
        if submission.get("verdict") and submission["status"] == "passed":
            return {
                "submission": submission,
                "verification": submission["verdict"],
                "release": None,
                "already_verified": True,
            }

        criteria = [Criterion.model_validate(item) for item in milestone["criteria"]]
        result = await self.verifier.verify(
            criteria=criteria,
            notes=submission.get("notes") or "",
            deliverables=submission.get("deliverables") or [],
        )
        status = {"pass": "passed", "revise": "needs_revision", "escalate": "needs_human"}[result.verdict]
        self.store.update_submission(
            submission_id, {"verdict": result.model_dump(), "status": status}
        )
        self._audit(
            actor="verifier",
            action="verification.completed",
            subject_type="milestone",
            subject_id=milestone["id"],
            decision=result.verdict,
            amount_cents=int(milestone["amount_cents"]),
            currency=milestone["currency"],
            reason_codes=result.failed,
            message=result.summary,
            meta={
                "submission_id": submission_id,
                "score": result.score,
                "engine": result.engine,
                "model": result.model,
                "criteria": [
                    {
                        "id": item.id,
                        "passed": item.passed,
                        "skipped": item.skipped,
                        "source": item.source,
                        "evidence": item.evidence,
                    }
                    for item in result.criteria
                ],
            },
        )

        release: dict | None = None
        if result.verdict == "pass":
            release = await self.release_milestone(milestone["id"], actor="verifier")
        elif result.verdict == "revise":
            self.store.update_milestone(milestone["id"], {"status": MilestoneStatus.REVISION.value})
        else:
            self.store.update_milestone(
                milestone["id"], {"status": MilestoneStatus.AWAITING_REVIEW.value}
            )
        self._publish("verified")
        submission = self.store.get_submission(submission_id)
        return {
            "submission": submission,
            "verification": result.model_dump(),
            "release": release,
            "already_verified": False,
        }

    async def release_milestone(self, milestone_id: str, *, actor: str = "verifier") -> dict:
        milestone = self.store.get_milestone(milestone_id)
        if not milestone:
            raise ServiceError("Milestone not found.")
        if milestone["status"] == MilestoneStatus.RELEASED.value:
            return {"status": "already_released", "milestone": milestone}
        job = self.store.get_job(milestone["job_id"], with_milestones=False)
        if not job:
            raise ServiceError("Job not found.")
        amount = int(milestone["amount_cents"])

        decision = self.engine.evaluate(
            SpendRequest(
                mandate_id=job["mandate_id"],
                amount_cents=amount,
                currency=milestone["currency"],
                category=job["category"],
                kind="capture",
                job_id=job["id"],
                milestone_id=milestone["id"],
                description=f"Release milestone '{milestone['title']}'",
            )
        )
        if decision.blocked:
            self.store.update_milestone(milestone["id"], {"status": MilestoneStatus.BLOCKED.value})
            self._audit(
                actor=actor,
                action="capture.blocked",
                subject_type="milestone",
                subject_id=milestone["id"],
                decision="block",
                amount_cents=amount,
                currency=milestone["currency"],
                reason_codes=decision.reason_codes,
                message="Capture blocked by mandate: " + ", ".join(decision.reason_codes),
                meta={"snapshot": decision.snapshot},
            )
            self._publish("blocked")
            return {"status": "blocked", "decision": decision.to_dict()}

        job_fin = self.store.job_financials(job["id"])
        authorization = job_fin.get("authorization")
        if not authorization:
            raise ServiceError("No active PayPal authorization for this job.")
        final = amount >= job_fin["remaining_cents"]

        try:
            capture = await self.paypal.capture_authorization(
                auth_id=str(authorization["paypal_id"]),
                amount_cents=amount,
                currency=milestone["currency"],
                final=final,
                idempotency_key=f"capture-{milestone['id']}",
            )
        except PayPalError as exc:
            self._audit(
                actor="paypal",
                action="paypal.error",
                subject_type="milestone",
                subject_id=milestone["id"],
                decision="error",
                amount_cents=amount,
                currency=milestone["currency"],
                message=f"Capture failed: {exc}",
                meta={"details": exc.details, "status": exc.status},
            )
            self.store.update_milestone(
                milestone["id"], {"status": MilestoneStatus.AWAITING_REVIEW.value}
            )
            self._publish("error")
            return {"status": "payment_error", "error": str(exc)}

        capture_payment = self.store.insert_payment(
            {
                "id": new_id("pay"),
                "kind": "capture",
                "status": "captured",
                "job_id": job["id"],
                "milestone_id": milestone["id"],
                "paypal_id": capture["id"],
                "related_id": authorization["paypal_id"],
                "amount_cents": amount,
                "fee_cents": 0,
                "currency": milestone["currency"],
                "description": f"Release for '{milestone['title']}'",
                "receipt": capture["raw"],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        self.store.update_milestone(
            milestone["id"],
            {"status": MilestoneStatus.RELEASED.value, "capture_id": capture_payment["id"]},
        )

        payout_result: dict[str, Any] = {"status": "skipped"}
        fee = amount * int(self.settings.platform_fee_bps) // 10000
        net = amount - fee
        if self.settings.paypal_payouts_enabled and job.get("freelancer_email") and net > 0:
            try:
                payout = await self.paypal.create_payout(
                    receiver_email=job["freelancer_email"],
                    amount_cents=net,
                    currency=milestone["currency"],
                    note=f"Payment for {job['title']} — {milestone['title']}",
                    sender_item_id=milestone["id"],
                    idempotency_key=f"payout-{milestone['id']}",
                )
                payout_payment = self.store.insert_payment(
                    {
                        "id": new_id("pay"),
                        "kind": "payout",
                        "status": "completed",
                        "job_id": job["id"],
                        "milestone_id": milestone["id"],
                        "paypal_id": payout["id"],
                        "related_id": capture["id"],
                        "amount_cents": net,
                        "fee_cents": fee,
                        "currency": milestone["currency"],
                        "description": f"Payout to {job['freelancer_email']}",
                        "receipt": payout["raw"],
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                    }
                )
                self.store.update_milestone(milestone["id"], {"payout_id": payout_payment["id"]})
                payout_result = {
                    "status": "completed",
                    "payout_id": payout["id"],
                    "payment_id": payout_payment["id"],
                    "net_cents": net,
                    "fee_cents": fee,
                }
                self._audit(
                    actor="paypal",
                    action="payout.completed",
                    subject_type="milestone",
                    subject_id=milestone["id"],
                    decision="allow",
                    amount_cents=net,
                    currency=milestone["currency"],
                    message=f"Paid {fmt_cents(net, milestone['currency'])} to {job['freelancer_email']}.",
                    meta={
                        "payout_id": payout["id"],
                        "receiver": job["freelancer_email"],
                        "fee_cents": fee,
                        "paypal_mode": self.paypal.mode,
                    },
                )
            except PayPalError as exc:
                payout_payment = self.store.insert_payment(
                    {
                        "id": new_id("pay"),
                        "kind": "payout",
                        "status": "failed",
                        "job_id": job["id"],
                        "milestone_id": milestone["id"],
                        "paypal_id": None,
                        "related_id": capture["id"],
                        "amount_cents": net,
                        "fee_cents": fee,
                        "currency": milestone["currency"],
                        "description": f"Payout failed for {job['freelancer_email']}",
                        "receipt": {"error": str(exc), "details": exc.details},
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                    }
                )
                payout_result = {
                    "status": "failed",
                    "payment_id": payout_payment["id"],
                    "error": str(exc),
                }
                self._audit(
                    actor="paypal",
                    action="payout.failed",
                    subject_type="milestone",
                    subject_id=milestone["id"],
                    decision="error",
                    amount_cents=net,
                    currency=milestone["currency"],
                    message=f"Capture succeeded but payout failed: {exc}",
                    meta={"capture_id": capture["id"], "details": exc.details},
                )
        else:
            self._audit(
                actor="system",
                action="payout.skipped",
                subject_type="milestone",
                subject_id=milestone["id"],
                decision="info",
                amount_cents=net,
                currency=milestone["currency"],
                message="Payouts are disabled; capture recorded without a Payouts transfer.",
            )

        milestones = self.store.list_milestones(job["id"])
        all_released = all(item["status"] == MilestoneStatus.RELEASED.value for item in milestones)
        if all_released:
            self.store.update_job(job["id"], {"status": JobStatus.COMPLETED.value})
        else:
            self.store.update_job(job["id"], {"status": JobStatus.IN_PROGRESS.value})

        self._audit(
            actor=actor,
            action="payment.released",
            subject_type="milestone",
            subject_id=milestone["id"],
            decision="allow",
            amount_cents=amount,
            currency=milestone["currency"],
            message=(
                f"Captured {fmt_cents(amount, milestone['currency'])} for '{milestone['title']}'. "
                + (
                    f"Paid freelancer {fmt_cents(net, milestone['currency'])}."
                    if payout_result["status"] == "completed"
                    else "Payout pending."
                )
            ),
            meta={
                "capture_id": capture["id"],
                "capture_payment_id": capture_payment["id"],
                "authorization_id": authorization["paypal_id"],
                "final_capture": final,
                "payout": payout_result,
                "paypal_mode": self.paypal.mode,
            },
        )
        self._publish("released")
        return {
            "status": "released",
            "milestone": self.store.get_milestone(milestone["id"]),
            "capture": capture_payment,
            "payout": payout_result,
            "final_capture": final,
        }

    async def human_release(self, milestone_id: str) -> dict:
        milestone = self.store.get_milestone(milestone_id)
        if not milestone:
            raise ServiceError("Milestone not found.")
        self._audit(
            actor="human",
            action="milestone.human_release",
            subject_type="milestone",
            subject_id=milestone_id,
            decision="allow",
            amount_cents=int(milestone["amount_cents"]),
            currency=milestone["currency"],
            message="Principal manually approved release after automated review.",
        )
        return await self.release_milestone(milestone_id, actor="human")

    async def cancel_job(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if not job:
            raise ServiceError("Job not found.")
        if job["status"] == JobStatus.COMPLETED.value:
            raise ServiceError("A completed job cannot be cancelled.")
        auth = self.store.job_authorization(job_id)
        if auth:
            try:
                await self.paypal.void_authorization(
                    auth_id=str(auth["paypal_id"]), idempotency_key=f"void-{job_id}"
                )
            except PayPalError as exc:
                raise ServiceError(f"Could not void the authorization: {exc}") from exc
            self.store.update_payment(auth["id"], {"status": "voided"})
        self.store.update_job(job_id, {"status": JobStatus.CANCELLED.value})
        self._audit(
            actor="human",
            action="job.cancelled",
            subject_type="job",
            subject_id=job_id,
            decision="block",
            amount_cents=int(job["total_cents"]),
            currency=job["currency"],
            message="Principal cancelled the job and released the hold.",
        )
        self._publish("cancelled")
        return {"status": "cancelled", "job": self.store.get_job(job_id)}

    # -- demo helpers -----------------------------------------------------
    def demo_submit(self, milestone_id: str, quality: str) -> dict:
        if not self.settings.demo_mode:
            raise ServiceError("Demo tools are disabled.")
        if quality not in DEMO_NOTES:
            raise ServiceError("Unknown demo quality.")
        request = SubmitWorkRequest(notes=DEMO_NOTES[quality], samples=DEMO_SAMPLES[quality])
        return self.submit_work(milestone_id, request, actor="freelancer", samples=DEMO_SAMPLES[quality])

    def reset_demo(self) -> dict:
        if not self.settings.demo_mode:
            raise ServiceError("Demo reset is disabled.")
        self.store.reset()
        if self.settings.uploads_dir.exists():
            for path in self.settings.uploads_dir.iterdir():
                if path.is_file():
                    path.unlink()
        self._audit(
            actor="system",
            action="demo.reset",
            decision="info",
            message="Demo state cleared. Audit chain restarted.",
        )
        self._publish("reset")
        return self.state()

    def tamper_audit(self) -> dict:
        if not self.settings.demo_mode:
            raise ServiceError("Tamper test is disabled.")
        row = self.store.audit_tamper()
        self._publish("tamper")
        verification = self.store.audit_verify()
        return {"tampered_seq": row["seq"] if row else None, "verification": verification}

    # -- uploads ----------------------------------------------------------
    def save_upload(self, filename: str, data: bytes, mime: str = "") -> dict:
        name = Path(filename or "upload").name[:120]
        extension = Path(name).suffix.lower().lstrip(".")
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ServiceError(f"Unsupported file type: .{extension or '?'}")
        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        if len(data) > max_bytes:
            raise ServiceError(f"File is too large (max {self.settings.max_upload_mb} MB).")
        if not data:
            raise ServiceError("The uploaded file is empty.")
        upload_id = new_id("upl")
        target = self.settings.uploads_dir / f"{upload_id}.{extension}"
        target.write_bytes(data)
        inspection = inspect_path(target, name)
        upload = self.store.insert_upload(
            {
                "id": upload_id,
                "name": name,
                "path": str(target),
                "mime": mime or inspection.get("mime", "application/octet-stream"),
                "size": len(data),
                "sha256": inspection.get("sha256", ""),
                "created_at": now_iso(),
            }
        )
        self._audit(
            actor="freelancer",
            action="upload.received",
            subject_type="upload",
            subject_id=upload_id,
            decision="info",
            message=f"Uploaded {name} ({len(data)} bytes).",
            meta={"sha256": upload["sha256"], "inspection": {k: inspection.get(k) for k in ("valid", "ext", "width", "height", "color_count")}},
        )
        self._publish("upload")
        return {**upload, "url": f"/api/uploads/{upload_id}/content"}

    def get_upload_file(self, upload_id: str) -> tuple[Path, str, str]:
        upload = self.store.get_upload(upload_id)
        if not upload:
            raise ServiceError("Upload not found.")
        path = Path(upload["path"])
        if not path.is_file():
            raise ServiceError("Uploaded file is missing.")
        return path, upload["mime"], upload["name"]

    # -- webhook ----------------------------------------------------------
    async def handle_webhook(self, headers: dict[str, str], event: dict) -> dict:
        verified = await self.paypal.verify_webhook(headers, event)
        event_type = str(event.get("event_type") or "unknown")
        resource = event.get("resource") or {}
        resource_id = str(resource.get("id") or "")
        self._audit(
            actor="paypal",
            action="webhook.received",
            subject_type="paypal_event",
            subject_id=resource_id,
            decision="info" if verified else "error",
            currency=str((resource.get("amount") or {}).get("currency_code") or ""),
            message=f"Webhook {event_type} ({'verified' if verified else 'unverified'}).",
            meta={"event_type": event_type, "verified": verified, "resource": resource},
        )
        if verified and resource_id:
            for payment in self.store.list_payments(500):
                if payment.get("paypal_id") == resource_id and event_type in {
                    "PAYMENT.CAPTURE.DENIED",
                    "PAYMENT.CAPTURE.REFUNDED",
                    "PAYMENT.PAYOUTS-ITEM.FAILED",
                }:
                    self.store.update_payment(payment["id"], {"status": "failed"})
        self._publish("webhook")
        return {"received": True, "verified": verified}

    def close(self) -> None:
        self.db.close()

    def discard_uploads(self) -> None:
        if self.settings.uploads_dir.exists():
            shutil.rmtree(self.settings.uploads_dir, ignore_errors=True)
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
