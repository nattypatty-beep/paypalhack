"""FastAPI application for MandatePay."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, Settings, load_settings
from .models import (
    AuthorizeJobRequest,
    CommandRequest,
    CreateJobRequest,
    CreateMandateRequest,
    ResolveApprovalRequest,
    SpendCheckRequest,
    SubmitWorkRequest,
)
from .service import AppService, ServiceError

log = logging.getLogger("mandatepay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class DemoSubmitRequest(BaseModel):
    quality: str = Field(default="final", pattern="^(concept|draft|final|brandkit)$")


# Endpoints that must stay reachable without Basic auth:
#   * /api/health      -> platform health checks
#   * /api/webhooks/*  -> PayPal cannot send a browser login; the webhook
#                         signature is verified inside the service instead.
PUBLIC_PATHS = {"/api/health", "/api/webhooks/paypal"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    service = AppService(settings)
    if bool(settings.basic_auth_user) != bool(settings.basic_auth_password):
        log.warning(
            "Basic auth disabled: set both BASIC_AUTH_USER and BASIC_AUTH_PASSWORD to enable it."
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.bootstrap()
        log.info(
            "MandatePay ready: paypal=%s llm=%s demo=%s",
            service.paypal.mode,
            service.llm.provider if service.llm.enabled else "rules",
            settings.demo_mode,
        )
        yield
        service.close()

    app = FastAPI(
        title="MandatePay",
        description="Agentic commerce with a leash: mandates, escrow and verified payouts.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.service = service

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if settings.auth_enabled and request.url.path not in PUBLIC_PATHS:
            authorized = False
            scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "basic" and credentials:
                try:
                    decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    decoded = ""
                username, separator, password = decoded.partition(":")
                if separator:
                    user_ok = hmac.compare_digest(
                        username.encode("utf-8"), settings.basic_auth_user.encode("utf-8")
                    )
                    password_ok = hmac.compare_digest(
                        password.encode("utf-8"), settings.basic_auth_password.encode("utf-8")
                    )
                    authorized = user_ok and password_ok
            if not authorized:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Authentication required."},
                    headers={"WWW-Authenticate": 'Basic realm="MandatePay", charset="UTF-8"'},
                )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": type(exc).__name__},
        )

    # -- pages ------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    settings.samples_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/samples", StaticFiles(directory=settings.samples_dir), name="samples")

    # -- health / state ---------------------------------------------------
    @app.get("/api/health")
    async def health():
        chain = service.store.audit_verify()
        return {
            "status": "ok",
            "app": settings.app_name,
            "paypal_mode": service.paypal.mode,
            "llm_enabled": service.llm.enabled,
            "llm_provider": service.llm.provider,
            "auth_enabled": settings.auth_enabled,
            "audit_entries": chain.get("count", 0),
            "audit_ok": chain.get("ok", False),
        }

    @app.get("/api/state")
    async def state():
        return service.state()

    @app.get("/api/audit")
    async def audit(limit: int = 300):
        return {
            "entries": service.store.list_audit(max(1, min(limit, 1000))),
            "verification": service.store.audit_verify(),
        }

    @app.get("/api/audit/verify")
    async def audit_verify():
        return service.store.audit_verify()

    @app.post("/api/audit/tamper")
    async def audit_tamper():
        return service.tamper_audit()

    @app.get("/api/stream")
    async def stream(request: Request):
        async def event_stream():
            queue = service.bus.subscribe()
            try:
                yield "retry: 2000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: state\ndata: {json.dumps(item)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                service.bus.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -- mandate / agent command -----------------------------------------
    @app.post("/api/command")
    async def command(request: CommandRequest):
        return await service.command(request.text, request.principal, request.post)

    @app.post("/api/mandates")
    async def create_mandate(request: CreateMandateRequest):
        return await service.create_mandate(request)

    # -- jobs -------------------------------------------------------------
    @app.post("/api/jobs")
    async def create_job(request: CreateJobRequest):
        return await service.create_job(request)

    @app.post("/api/jobs/{job_id}/authorize")
    async def authorize_job(job_id: str, request: AuthorizeJobRequest | None = None):
        return await service.authorize_job(job_id, request.approval_id if request else None)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return await service.cancel_job(job_id)

    # -- approvals --------------------------------------------------------
    @app.get("/api/approvals/{approval_id}")
    async def get_approval(approval_id: str):
        approval = service.store.get_approval(approval_id)
        if not approval:
            raise ServiceError("Approval not found.")
        return approval

    @app.post("/api/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, request: ResolveApprovalRequest):
        return service.resolve_approval(approval_id, request)

    # -- spend simulator --------------------------------------------------
    @app.post("/api/spend/check")
    async def spend_check(request: SpendCheckRequest):
        return service.check_spend(request)

    # -- submissions ------------------------------------------------------
    @app.post("/api/milestones/{milestone_id}/submissions")
    async def submit_work(milestone_id: str, request: SubmitWorkRequest):
        return service.submit_work(milestone_id, request)

    @app.post("/api/submissions/{submission_id}/verify")
    async def verify_submission(submission_id: str):
        return await service.verify_submission(submission_id)

    @app.post("/api/milestones/{milestone_id}/release")
    async def human_release(milestone_id: str):
        return await service.human_release(milestone_id)

    @app.post("/api/demo/milestones/{milestone_id}/submit")
    async def demo_submit(milestone_id: str, request: DemoSubmitRequest | None = None):
        quality = request.quality if request else "final"
        return service.demo_submit(milestone_id, quality)

    # -- uploads ----------------------------------------------------------
    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        max_bytes = settings.max_upload_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        return service.save_upload(file.filename or "upload", data, file.content_type or "")

    @app.get("/api/uploads/{upload_id}/content")
    async def upload_content(upload_id: str):
        path, mime, name = service.get_upload_file(upload_id)
        return FileResponse(
            path,
            media_type=mime or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{name}"',
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                "Cache-Control": "private, max-age=60",
            },
        )

    # -- demo / dev tools -------------------------------------------------
    @app.post("/api/demo/reset")
    async def demo_reset():
        return service.reset_demo()

    # -- webhooks ---------------------------------------------------------
    @app.post("/api/webhooks/paypal")
    async def paypal_webhook(request: Request):
        try:
            event = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})
        headers = {key.lower(): value for key, value in request.headers.items()}
        return await service.handle_webhook(headers, event)

    return app


app = create_app()
