"""Schedule profile and opportunity triage (FR-09).

Three endpoints, no writes to Gmail:

- `GET /schedule`  — what the user has told us about their week
- `PUT /schedule`  — replace it
- `POST /opportunities/assess` — "should I do this?"

The schedule is the only state the user owns rather than the agent, so it is
the only thing here that is stored rather than derived.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.schemas import (
    OpportunityAssessment,
    OpportunityRequest,
    ScheduleProfile,
    TimetableImport,
    TimetableImportRequest,
)
from backend.services import store, timetable, triage

log = logging.getLogger(__name__)

router = APIRouter(tags=["opportunities"])


@router.get("/schedule", response_model=ScheduleProfile)
def get_schedule() -> ScheduleProfile:
    """Return the saved weekly schedule, or an empty unconfigured one."""
    return store.load_schedule()


@router.put("/schedule", response_model=ScheduleProfile)
def put_schedule(profile: ScheduleProfile) -> ScheduleProfile:
    """Replace the weekly schedule.

    `configured` is recomputed from `busy_blocks` on save; a client cannot
    declare itself configured without saying when it is busy.
    """
    return store.save_schedule(profile)


@router.post("/opportunities/assess", response_model=OpportunityAssessment)
def assess_opportunity(request: OpportunityRequest) -> OpportunityAssessment:
    """Decide whether an invitation is worth attending, given the user's week."""
    return triage.assess(request.message_id)


@router.post("/schedule/import", response_model=TimetableImport)
def import_timetable(request: TimetableImportRequest) -> TimetableImport:
    """Read a photo of a timetable into proposed busy blocks.

    Returns the blocks; it does not save them. The user checks them in the
    editor first, because a misread timetable would quietly corrupt every
    "should I attend" answer afterwards.
    """
    return timetable.extract(request.image_base64, request.media_type)
