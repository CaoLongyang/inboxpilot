"""The single source of truth for data exchanged between backend and frontend.

Owner: A (backend) maintains this file; B must not redefine these shapes in the
frontend. If a field needs to change, change it here first and tell the other
person the same day.

Design rules
------------
1. Every important claim carries `evidence` pointing back to the source email.
2. `kind` separates what the email says (explicit) from what the model guesses
   (inference) from what is simply unknown. The UI must render them differently.
3. Nothing in this file can send email. The only write action is `create_draft`.
4. Unknown fields are dropped, so a malicious or malformed model response
   cannot smuggle extra instructions into the execution path.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class ClaimKind(str, Enum):
    """How well supported a statement is by the source email."""

    EXPLICIT = "explicit"      # literally stated in the email
    INFERENCE = "inference"    # reasonably derived from explicit facts
    UNKNOWN = "unknown"        # not enough evidence; must not be invented


class EmailCategory(str, Enum):
    ACTION_REQUIRED = "action_required"
    INFORMATION_ONLY = "information_only"
    UNCERTAIN = "uncertain"


class ActionKind(str, Enum):
    CREATE_DRAFT = "create_draft"
    NONE = "none"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    CREATED = "created"
    FAILED = "failed"
    REJECTED = "rejected"


# --------------------------------------------------------------------------
# Evidence and analysis (FR-03 / FR-04 / FR-05)
# --------------------------------------------------------------------------


class Evidence(APIModel):
    """A verbatim quote from the source email. Not a paraphrase."""

    # Stamped by the server from the email actually read. The model is never
    # asked to echo it: it adds a failure mode with no benefit, and a model that
    # hallucinated the id would silently break the link back to the source.
    message_id: str = ""
    field: Literal["subject", "body", "snippet"] = "body"
    quote: str = Field(min_length=1, max_length=1000)


class Deadline(APIModel):
    """A due date, including how confident we are about it."""

    text: str | None = Field(default=None, description="Date as written in the email")
    date: Date | None = Field(default=None, description="Resolved ISO date, if unambiguous")
    kind: ClaimKind = ClaimKind.UNKNOWN
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unknown_has_no_date(self) -> "Deadline":
        if self.kind is ClaimKind.UNKNOWN and self.date is not None:
            raise ValueError("An UNKNOWN deadline must not carry a resolved date")
        return self


class Fact(APIModel):
    text: str
    kind: ClaimKind
    evidence: list[Evidence] = Field(default_factory=list)


class ActionItem(APIModel):
    title: str
    why: str = Field(description="Why this is required, grounded in the email")
    kind: ClaimKind = ClaimKind.EXPLICIT
    deadline: Deadline | None = None
    risk: str | None = Field(default=None, description="What happens if it is ignored")
    needs_user_input: list[str] = Field(
        default_factory=list, description="Info the user must supply; never invented"
    )
    evidence: list[Evidence] = Field(default_factory=list)


class EmailAnalysis(APIModel):
    message_id: str
    thread_id: str | None = None
    summary: str
    category: EmailCategory
    action_required: bool
    key_facts: list[Fact] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    uncertainties: list[str] = Field(
        default_factory=list, description="Explicitly unknown information"
    )
    risk_summary: str | None = None
    model: str
    analyzed_at: datetime = Field(default_factory=now_utc)


class ModelUsage(APIModel):
    """Recorded for every inference call (FR-10 / technical validation)."""

    model: str
    latency_ms: int = Field(ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


# --------------------------------------------------------------------------
# Email payloads (FR-01 / FR-02)
# --------------------------------------------------------------------------


class EmailSummaryItem(APIModel):
    id: str
    thread_id: str | None = None
    subject: str = ""
    sender: str = Field(default="", alias="from")
    recipients: list[str] = Field(default_factory=list, alias="to")
    date: datetime | None = None
    snippet: str = ""
    unread: bool = False
    labels: list[str] = Field(default_factory=list)


class EmailDetail(EmailSummaryItem):
    body_text: str = ""
    body_html: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class EmailListResponse(APIModel):
    emails: list[EmailSummaryItem] = Field(default_factory=list)
    count: int = 0
    next_page_token: str | None = None


# --------------------------------------------------------------------------
# Action planning and the approval gate (FR-06 / FR-07 / FR-08 / FR-09)
# --------------------------------------------------------------------------


class DraftProposal(APIModel):
    """A draft the user must approve. It is never sent automatically."""

    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    in_reply_to: str | None = Field(default=None, description="Gmail message id being replied to")
    thread_id: str | None = None

    @model_validator(mode="after")
    def _needs_recipient(self) -> "DraftProposal":
        if not [a for a in self.to if a.strip()]:
            raise ValueError("A draft needs at least one recipient")
        return self


class ProposedAction(APIModel):
    id: str = Field(description="Server-generated proposal id, required for approval")
    message_id: str
    kind: ActionKind = ActionKind.NONE
    title: str
    why: str
    requires_scopes: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(
        default_factory=list, description="Blocks execution until the user supplies it"
    )
    draft: DraftProposal | None = None


class AnalyzeRequest(APIModel):
    message_id: str
    force_refresh: bool = False


class AnalyzeResponse(APIModel):
    analysis: EmailAnalysis
    usage: ModelUsage | None = None
    cached: bool = False


class PlanRequest(APIModel):
    message_id: str


class PlanResponse(APIModel):
    message_id: str
    actions: list[ProposedAction] = Field(default_factory=list)


class DraftCreateRequest(APIModel):
    """The user's approval. The body may be edited by the user before approval."""

    proposal_id: str
    idempotency_key: str = Field(min_length=8, max_length=128)
    draft: DraftProposal
    approved_by: str = Field(min_length=1, description="Identity of the approving user")
    approved: bool = Field(
        default=False, description="Must be true; the backend rejects otherwise"
    )


class ExecutionResult(APIModel):
    action_id: str
    status: ExecutionStatus
    draft_id: str | None = Field(default=None, description="Gmail draft id returned by the API")
    verified: bool = Field(default=False, description="Draft was read back from Gmail")
    message: str = ""
    created_at: datetime = Field(default_factory=now_utc)


# --------------------------------------------------------------------------
# The user's own timetable, and whether an invitation fits it (FR-09)
# --------------------------------------------------------------------------


class BusyBlock(APIModel):
    """A recurring weekly commitment: a lecture, a shift, a standing meeting.

    Deliberately recurring and weekly rather than a calendar import. The point
    is that the user can answer "when are you normally busy?" in thirty
    seconds, without handing over a Google Calendar scope we do not need.
    """

    weekday: int = Field(ge=0, le=6, description="0 = Monday, 6 = Sunday")
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM")
    label: str = ""

    @model_validator(mode="after")
    def _ends_after_start(self):
        # Overnight blocks (22:00-02:00) are rejected rather than silently
        # misread: a wrong answer about a clash is worse than "please split it".
        if self.end <= self.start:
            raise ValueError("end must be later than start; split overnight blocks in two")
        return self


class ScheduleProfile(APIModel):
    """What the user tells us about their own week."""

    role: Literal["student", "worker", "other"] = "student"
    timezone: str = "Asia/Shanghai"
    busy_blocks: list[BusyBlock] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Free text, e.g. 'only paid AI hackathons matter to me'",
    )
    value_threshold: int = Field(
        default=4,
        ge=1,
        le=5,
        description="How good an event must be (1-5) to be worth a timetable clash",
    )
    configured: bool = Field(
        default=False, description="Derived: has the user actually told us anything?"
    )


class EventWindow(APIModel):
    """When the thing in the email happens, in the user's local time."""

    name: str = ""
    start: datetime | None = None
    end: datetime | None = None
    commitment_hours: float | None = None
    location: str = ""
    precision: Literal["exact", "date_only", "unknown"] = "unknown"


class Conflict(APIModel):
    """One concrete clash between the event and a busy block."""

    busy_label: str = ""
    weekday: int = Field(ge=0, le=6)
    on_date: Date
    start: datetime
    end: datetime


class ValueAssessment(APIModel):
    score: int = Field(ge=1, le=5)
    reasons: list[str] = Field(default_factory=list)


class TriageVerdict(str, Enum):
    """What to do about an invitation, once the timetable has been consulted."""

    ATTEND = "attend"                                # free, worth it
    ATTEND_DESPITE_CONFLICT = "attend_despite_conflict"  # clashes, still worth it
    DECLINE_CONFLICT = "decline_conflict"            # clashes, not worth it
    SKIP_NOT_WORTH_IT = "skip_not_worth_it"          # free, but not worth the time
    UNKNOWN_TIMING = "unknown_timing"                # email does not say when
    NOT_AN_EVENT = "not_an_event"                    # nothing to attend
    NO_SCHEDULE = "no_schedule"                      # we must ask the user first


class OpportunityAssessment(APIModel):
    """The answer to "should I do this?", with the evidence for it."""

    message_id: str
    verdict: TriageVerdict
    headline: str = Field(default="", description="One imperative line for the UI")
    is_opportunity: bool = False
    event: EventWindow | None = None
    value: ValueAssessment | None = None
    conflicts: list[Conflict] = Field(default_factory=list)
    reasoning: str = ""
    missing: list[str] = Field(default_factory=list)
    usage: ModelUsage | None = None


class OpportunityRequest(APIModel):
    message_id: str = Field(min_length=1)


# --------------------------------------------------------------------------
# Infrastructure
# --------------------------------------------------------------------------


class HealthResponse(APIModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    integrations: dict[str, bool] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=now_utc)


class ErrorDetail(APIModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(APIModel):
    error: ErrorDetail
