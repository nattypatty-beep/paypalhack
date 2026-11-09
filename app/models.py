"""Pydantic models used by the API, planner and verifier."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Verdict(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class JobStatus(str, Enum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHORIZED = "authorized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    PAYMENT_ERROR = "payment_error"
    CANCELLED = "cancelled"


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    REVISION = "revision"
    AWAITING_REVIEW = "awaiting_review"
    RELEASED = "released"
    BLOCKED = "blocked"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


CRITERION_TYPES = {
    "file_format",
    "min_dimensions",
    "vector_source",
    "color_palette_max",
    "transparent_background",
    "placeholder_free",
    "keyword_coverage",
    "concepts_count",
    "notes_min_words",
    "text_min_words",
    "license_included",
    "file_count",
    "text_contains",
    "max_file_size_mb",
    "qualitative",
    "human_review",
}


class Criterion(BaseModel):
    """A single, ideally machine-checkable, acceptance criterion."""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    ai_assisted: bool = False

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in CRITERION_TYPES:
            raise ValueError(f"unknown criterion type: {value}")
        return value


class MilestonePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    description: str = ""
    amount_cents: int = Field(gt=0)
    criteria: list[Criterion] = Field(default_factory=list)
    due_in_days: int = 7

    @field_validator("criteria")
    @classmethod
    def _needs_criteria(cls, value: list[Criterion]) -> list[Criterion]:
        if not value:
            raise ValueError("a milestone needs at least one acceptance criterion")
        return value


class JobPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    summary: str = ""
    milestones: list[MilestonePlan]
    rationale: str = ""
    engine: str = "deterministic"

    @field_validator("milestones")
    @classmethod
    def _needs_milestones(cls, value: list[MilestonePlan]) -> list[MilestonePlan]:
        if not value:
            raise ValueError("a job needs at least one milestone")
        return value

    @property
    def total_cents(self) -> int:
        return sum(m.amount_cents for m in self.milestones)


# --------------------------------------------------------------------------
# API requests
# --------------------------------------------------------------------------


class CommandRequest(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    principal: str = "you"
    post: bool = True


class CreateMandateRequest(BaseModel):
    principal: str = "you"
    currency: str = "USD"
    budget_cents: int = Field(gt=0)
    approval_threshold_cents: int = Field(ge=0)
    per_txn_limit_cents: int | None = Field(default=None, ge=0)
    daily_limit_cents: int | None = Field(default=None, ge=0)
    categories: list[str] = Field(default_factory=list)
    expires_in_days: int = Field(default=7, ge=1, le=365)
    raw_text: str = ""


class CreateJobRequest(BaseModel):
    mandate_id: str
    title: str | None = None
    brief: str = ""
    category: str = "design"
    amount_cents: int = Field(gt=0)
    currency: str | None = None
    freelancer_name: str | None = None
    freelancer_email: str | None = None
    plan: JobPlan | None = None
    auto_authorize: bool = False


class AuthorizeJobRequest(BaseModel):
    approval_id: str | None = None


class SubmitWorkRequest(BaseModel):
    notes: str = Field(default="", max_length=20000)
    samples: list[str] = Field(default_factory=list)
    upload_ids: list[str] = Field(default_factory=list)

    @field_validator("samples")
    @classmethod
    def _safe_samples(cls, values: list[str]) -> list[str]:
        for name in values:
            if "/" in name or "\\" in name or ".." in name:
                raise ValueError("samples must be plain file names")
        return values


class ResolveApprovalRequest(BaseModel):
    approve: bool = True
    note: str = Field(default="", max_length=2000)


class SpendCheckRequest(BaseModel):
    mandate_id: str
    amount_cents: int
    category: str = ""
    description: str = "Agent spend check"
    record: bool = True


# --------------------------------------------------------------------------
# Verification results
# --------------------------------------------------------------------------


class CriterionResult(BaseModel):
    id: str
    label: str
    type: str
    passed: bool
    skipped: bool = False
    weight: float = 1.0
    evidence: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    source: Literal["rules", "ai", "human"] = "rules"


class VerificationResult(BaseModel):
    verdict: Literal["pass", "revise", "escalate"]
    score: float = Field(ge=0, le=1)
    summary: str = ""
    criteria: list[CriterionResult] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    engine: str = "rules"
    model: str | None = None
    ai_notes: str = ""
    checked_at: str = ""


class Deliverable(BaseModel):
    id: str
    name: str
    path: str = ""
    url: str = ""
    mime: str = "application/octet-stream"
    size: int = 0
    sha256: str = ""
    inspection: dict[str, Any] = Field(default_factory=dict)
