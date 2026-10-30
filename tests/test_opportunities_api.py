"""HTTP contract for the schedule and triage endpoints (FR-09).

The service layer is covered in test_triage.py; these tests pin the shapes and
status codes the frontend switches on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services import store


@pytest.fixture
def client(temp_db) -> TestClient:
    return TestClient(create_app())


def test_a_fresh_install_reports_an_unconfigured_schedule(client) -> None:
    body = client.get("/schedule").json()
    assert body["configured"] is False
    assert body["busy_blocks"] == []
    assert body["value_threshold"] == 4


def test_a_saved_schedule_comes_back(client) -> None:
    payload = {
        "role": "student",
        "timezone": "Asia/Shanghai",
        "busy_blocks": [
            {"weekday": 1, "start": "09:00", "end": "11:00", "label": "Algorithms"},
        ],
        "notes": "only paid AI events",
        "value_threshold": 4,
    }
    saved = client.put("/schedule", json=payload)
    assert saved.status_code == 200
    assert saved.json()["configured"] is True

    again = client.get("/schedule").json()
    assert again["busy_blocks"][0]["label"] == "Algorithms"
    assert again["notes"] == "only paid AI events"


def test_a_client_cannot_declare_itself_configured(client) -> None:
    """`configured` is derived from the blocks, not taken from the request."""
    saved = client.put(
        "/schedule", json={"configured": True, "busy_blocks": []}
    ).json()
    assert saved["configured"] is False


def test_an_overnight_block_is_a_422_with_the_envelope(client) -> None:
    response = client.put(
        "/schedule",
        json={"busy_blocks": [{"weekday": 0, "start": "22:00", "end": "02:00"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_assess_without_a_schedule_asks_for_one_instead_of_failing(client) -> None:
    response = client.post("/opportunities/assess", json={"message_id": "msg-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "no_schedule"
    assert body["headline"]


def test_assess_requires_a_message_id(client) -> None:
    assert client.post("/opportunities/assess", json={"message_id": ""}).status_code == 422


def test_the_schedule_survives_a_reload_of_the_store(client) -> None:
    """The profile is durable state, not a session variable."""
    client.put(
        "/schedule",
        json={"busy_blocks": [{"weekday": 2, "start": "08:00", "end": "10:00"}]},
    )
    assert len(store.load_schedule().busy_blocks) == 1
