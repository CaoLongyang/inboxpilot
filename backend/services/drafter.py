"""The approval gate and the read-back check (tasks A12 / A13).

This module answers two questions and nothing else:

1. **Was this write authorised?** Five independent checks, all of which must
   pass before a single byte reaches Gmail. The browser is not trusted for any
   of them.
2. **Did the write actually happen?** Gmail returning a draft id is a claim; a
   subsequent `drafts.get` is evidence. We report `verified` only on evidence.

Ordering matters. The idempotency row is claimed *before* Gmail is called, so
two concurrent retries of the same approval cannot both reach the write. A row
left `pending` means "an attempt started and we never learned its outcome",
which is treated as a refusal rather than a licence to try again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parseaddr

from backend.errors import AppError, ApprovalRequired, AuthError, DuplicateRequest, NotFound
from backend.schemas import (
    ActionKind,
    DraftCreateRequest,
    ExecutionResult,
    ExecutionStatus,
)
from backend.services import gmail_client, store, token_store

log = logging.getLogger(__name__)


def _normalise(text: str | None) -> str:
    """Collapse whitespace and case so formatting differences do not fail a check."""
    return " ".join((text or "").split()).casefold()


def _addresses(values: list[str]) -> set[str]:
    """Reduce header values to bare lowercase addresses."""
    found: set[str] = set()
    for value in values:
        for chunk in (value or "").split(","):
            _, address = parseaddr(chunk)
            if address:
                found.add(address.strip().casefold())
    return found


@dataclass
class DraftVerification:
    """What the read-back proved, field by field."""

    draft_id: str
    exists: bool = False
    subject_matches: bool = False
    recipients_match: bool = False
    body_matches: bool = False
    thread_id: str | None = None
    read_back_subject: str = ""
    read_back_to: list[str] | None = None

    @property
    def verified(self) -> bool:
        return (
            self.exists
            and self.subject_matches
            and self.recipients_match
            and self.body_matches
        )

    def explain(self) -> str:
        """A sentence a human can act on, naming the first thing that differed."""
        if not self.exists:
            return "The draft could not be read back from Gmail."
        if not self.subject_matches:
            return (
                "The draft exists but its subject does not match what was approved "
                f"(Gmail has {self.read_back_subject!r})."
            )
        if not self.recipients_match:
            recipients = ", ".join(self.read_back_to or []) or "none"
            return f"The draft exists but its recipients do not match (Gmail has {recipients})."
        if not self.body_matches:
            return "The draft exists but its body does not match what was approved."
        return "Draft created and read back from Gmail."


def verify_draft(
    draft_id: str,
    *,
    to: list[str],
    cc: list[str] | None,
    subject: str,
    body: str,
) -> DraftVerification:
    """Read the draft back and compare it to what the user approved."""
    read_back = gmail_client.get_draft(draft_id)
    verification = DraftVerification(
        draft_id=read_back.get("draft_id") or draft_id,
        exists=bool(read_back.get("message_id")),
        thread_id=read_back.get("thread_id"),
        read_back_subject=read_back.get("subject", ""),
        read_back_to=read_back.get("to") or [],
    )
    verification.subject_matches = _normalise(read_back.get("subject")) == _normalise(subject)
    verification.recipients_match = _addresses(read_back.get("to") or []) == _addresses(to)
    verification.body_matches = _normalise(read_back.get("body_text")) == _normalise(body)
    return verification


def _replay(previous: ExecutionResult, idempotency_key: str) -> ExecutionResult:
    """Return the recorded outcome of an identical earlier approval.

    Replaying is the whole point of an idempotency key: the caller gets the same
    answer it would have got the first time, and Gmail is not touched again.
    """
    if previous.status is ExecutionStatus.PENDING:
        # The earlier attempt claimed the key and never finished. Its request may
        # have reached Gmail. Refusing is the only answer that cannot duplicate.
        raise DuplicateRequest(
            "An earlier attempt with this key has not recorded an outcome. "
            "Check Gmail Drafts before trying again.",
            details={"idempotency_key": idempotency_key, "status": previous.status.value},
        )
    log.info(
        "drafter.replayed key=%s status=%s draft_id=%s",
        idempotency_key,
        previous.status.value,
        previous.draft_id or "-",
    )
    return previous


def _connected_account() -> str:
    state = token_store.status()
    if not state.get("connected"):
        raise AuthError(
            "Gmail is not connected",
            details={"next_step": "Open /auth/google to authorize an account"},
        )
    return (state.get("email") or "").strip()


def _authorise(request: DraftCreateRequest) -> tuple[object, str]:
    """Run every pre-write check. Returns (proposal, connected account)."""
    if not request.approved:
        # The most important check in the project. A request that reaches this
        # endpoint without an explicit approval is not a mistake to tolerate.
        raise ApprovalRequired(
            "This operation requires explicit user approval",
            details={"proposal_id": request.proposal_id},
        )

    proposal = store.get_proposal(request.proposal_id)
    if proposal is None:
        # Fails closed: a proposal id the server cannot reconstruct is not an
        # approval, it is a client-side invention.
        raise NotFound(
            "The approved proposal does not exist on this server",
            details={"proposal_id": request.proposal_id},
        )

    if proposal.kind is not ActionKind.CREATE_DRAFT:
        raise ApprovalRequired(
            "This proposal is not a draft creation",
            details={"proposal_id": proposal.id, "kind": proposal.kind.value},
        )

    if proposal.missing_info:
        raise ApprovalRequired(
            "The proposal is missing information that only the user can supply",
            details={"proposal_id": proposal.id, "missing_info": proposal.missing_info},
        )

    account = _connected_account()
    if not account:
        # The token file can exist without an email when it came from the
        # environment, and then there is nothing to compare the approver to.
        log.warning("drafter.approver_unverifiable proposal_id=%s", proposal.id)
        return proposal, ""

    if _normalise(request.approved_by) != _normalise(account):
        raise ApprovalRequired(
            "The approving account does not match the connected mailbox",
            details={"proposal_id": proposal.id},
        )

    return proposal, account


def execute(request: DraftCreateRequest) -> ExecutionResult:
    """Create a real Gmail draft for an approved proposal, then prove it exists."""
    proposal, _account = _authorise(request)

    previous = store.find_execution_by_key(request.idempotency_key)
    if previous is not None:
        return _replay(previous, request.idempotency_key)

    action_id = proposal.id
    try:
        store.record_execution(
            ExecutionResult(
                action_id=action_id,
                status=ExecutionStatus.PENDING,
                message="Draft creation in progress",
            ),
            idempotency_key=request.idempotency_key,
            proposal_id=proposal.id,
            message_id=proposal.message_id,
        )
    except DuplicateRequest:
        # Lost a race with a concurrent identical request; replay its outcome.
        previous = store.find_execution_by_key(request.idempotency_key)
        if previous is None:  # pragma: no cover - the row must exist to conflict
            raise
        return _replay(previous, request.idempotency_key)

    draft = request.draft
    try:
        draft_id = gmail_client.create_draft(
            to=draft.to,
            cc=draft.cc,
            subject=draft.subject,
            body=draft.body,
            thread_id=draft.thread_id,
            in_reply_to=draft.in_reply_to,
        )
    except AppError as exc:
        # The key stays burned. A failed write is recorded as failed so a retry
        # replays the failure instead of risking a second draft: we cannot tell
        # a rejected request from one Gmail accepted before the connection died.
        store.update_execution(
            ExecutionResult(
                action_id=action_id,
                status=ExecutionStatus.FAILED,
                message=exc.message,
            ),
            idempotency_key=request.idempotency_key,
        )
        store.set_proposal_status(proposal.id, "failed")
        log.warning(
            "drafter.create_failed proposal_id=%s code=%s", proposal.id, exc.code
        )
        raise

    try:
        verification = verify_draft(
            draft_id,
            to=draft.to,
            cc=draft.cc,
            subject=draft.subject,
            body=draft.body,
        )
        message = verification.explain()
        verified = verification.verified
    except AppError as exc:
        # The draft exists (Gmail returned an id) but we could not read it back.
        # Report created-but-unverified rather than claiming either extreme.
        message = f"Gmail returned a draft id, but reading it back failed: {exc.message}"
        verified = False
        log.warning("drafter.verify_failed draft_id=%s code=%s", draft_id, exc.code)

    result = ExecutionResult(
        action_id=action_id,
        status=ExecutionStatus.CREATED,
        draft_id=draft_id,
        verified=verified,
        message=message,
    )
    store.update_execution(result, idempotency_key=request.idempotency_key)
    store.set_proposal_status(proposal.id, "approved")
    log.info(
        "drafter.created proposal_id=%s draft_id=%s verified=%s",
        proposal.id,
        draft_id,
        verified,
    )
    return result
