"""Action planning and the approval gate (FR-06/07/08/09, tasks A12-A13).

The three handlers here are deliberately thin: the interesting logic lives in
`services.planner` (what to propose) and `services.drafter` (whether a write is
authorised, and whether it actually happened). Keeping them out of the router is
what makes them testable without HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.errors import NotFound
from backend.schemas import (
    DraftCreateRequest,
    ExecutionResult,
    PlanRequest,
    PlanResponse,
)
from backend.services import drafter, planner, store

router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/plan", response_model=PlanResponse)
def plan_actions(payload: PlanRequest) -> PlanResponse:
    """Propose next steps for one email.

    Returns at most one `create_draft` proposal, already stored server-side. An
    empty list is a valid, honest answer: some emails do not need a reply, and a
    system that always proposes something is a system that invents work.

    Declared sync: it reads Gmail and calls the reasoning model, both blocking.
    """
    return planner.plan(payload.message_id)


@router.post("/draft", response_model=ExecutionResult)
def create_draft(payload: DraftCreateRequest) -> ExecutionResult:
    """The approval gate: create a real Gmail draft (FR-07/08/09).

    Every check happens before the write, and the result is read back from Gmail
    before success is reported. See `services.drafter` for the five checks and
    the ordering that makes concurrent retries safe.
    """
    return drafter.execute(payload)


@router.get("/{action_id}", response_model=ExecutionResult)
def get_execution(action_id: str) -> ExecutionResult:
    """Execution status for a previously approved action (FR-09)."""
    result = store.get_execution(action_id)
    if result is None:
        raise NotFound(
            "No execution was recorded for this action",
            details={"action_id": action_id},
        )
    return result
