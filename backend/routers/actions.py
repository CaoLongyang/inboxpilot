from __future__ import annotations

from fastapi import APIRouter

from backend.errors import NotImplementedYet
from backend.schemas import (
    DraftCreateRequest,
    ExecutionResult,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/plan", response_model=PlanResponse)
async def plan_actions(payload: PlanRequest) -> PlanResponse:
    """Propose next steps for one email (FR-06, task B9 + A8)."""
    raise NotImplementedYet("POST /actions/plan lands on Day 3")


@router.post("/draft", response_model=ExecutionResult)
async def create_draft(payload: DraftCreateRequest) -> ExecutionResult:
    """The approval gate: create a real Gmail draft (FR-07/08/09, tasks A11-A13).

    Non-negotiable checks before any write:
    1. payload.approved is true
    2. proposal_id exists and belongs to the same message/account
    3. idempotency_key has not been used before
    4. recipients pass validation
    5. result is read back from Gmail before reporting success
    """
    raise NotImplementedYet("POST /actions/draft lands on Day 4 (tasks A11-A13)")


@router.get("/{action_id}", response_model=ExecutionResult)
async def get_execution(action_id: str) -> ExecutionResult:
    """Execution status for a previously approved action (FR-09)."""
    raise NotImplementedYet("GET /actions/{id} lands on Day 4 (task A13)")
