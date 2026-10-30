"""HTTP-level contract for the three action endpoints.

The service layer is already covered in depth; these tests exist to prove the
status codes and the error envelope, because the frontend switches on
`error.code` and a wrong status turns a clean refusal into a mystery.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.schemas import ActionKind, DraftProposal, PlanResponse, ProposedAction
from backend.services import planner, store


@pytest.fixture
def client(temp_db) -> TestClient:
    return TestClient(create_app())


def test_draft_without_approval_is_a_403_with_the_envelope(client) -> None:
    response = client.post(
        "/actions/draft",
        json={
            "proposal_id": "prop-1",
            "idempotency_key": "key-0123456789ab",
            "approved_by": "a@b.co",
            "approved": False,
            "draft": {"to": ["a@b.co"], "subject": "s", "body": "b"},
        },
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "approval_required"
    assert error["request_id"]


def test_draft_for_an_unknown_proposal_is_a_404(client) -> None:
    response = client.post(
        "/actions/draft",
        json={
            "proposal_id": "prop-missing",
            "idempotency_key": "key-0123456789ab",
            "approved_by": "a@b.co",
            "approved": True,
            "draft": {"to": ["a@b.co"], "subject": "s", "body": "b"},
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_draft_rejects_a_too_short_idempotency_key(client) -> None:
    """8 characters minimum: a guessable key is not an idempotency guarantee."""
    response = client.post(
        "/actions/draft",
        json={
            "proposal_id": "prop-1",
            "idempotency_key": "short",
            "approved_by": "a@b.co",
            "approved": True,
            "draft": {"to": ["a@b.co"], "subject": "s", "body": "b"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_execution_id_is_a_404(client) -> None:
    response = client.get("/actions/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_execution_can_be_queried_after_it_is_recorded(client) -> None:
    from backend.schemas import ExecutionResult, ExecutionStatus

    store.record_execution(
        ExecutionResult(
            action_id="prop-9",
            status=ExecutionStatus.CREATED,
            draft_id="draft-9",
            verified=True,
            message="Draft created and read back from Gmail.",
        ),
        idempotency_key="key-999999999999",
        proposal_id="prop-9",
        message_id="msg-9",
    )
    response = client.get("/actions/prop-9")
    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == "draft-9"
    assert body["verified"] is True


def test_plan_returns_the_stored_proposal(client, monkeypatch) -> None:
    proposal = ProposedAction(
        id="prop-7",
        message_id="msg-7",
        kind=ActionKind.CREATE_DRAFT,
        title="Reply",
        why="Because the email asks a question.",
        draft=DraftProposal(to=["a@b.co"], subject="Re: hi", body="ok"),
    )
    monkeypatch.setattr(
        planner, "plan", lambda _mid: PlanResponse(message_id="msg-7", actions=[proposal])
    )
    response = client.post("/actions/plan", json={"message_id": "msg-7"})
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert actions[0]["id"] == "prop-7"
    assert actions[0]["kind"] == "create_draft"
