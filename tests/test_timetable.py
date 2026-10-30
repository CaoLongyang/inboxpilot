"""Reading a timetable out of an image (FR-09 import path).

The model is stubbed. What is tested is the part that decides whether a row the
model returned becomes a busy block: day names in two languages, time formats,
and — most importantly — that a bad row is dropped and reported instead of
silently becoming a commitment.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from backend.errors import AppError, UpstreamError
from backend.main import create_app
from backend.schemas import ModelUsage
from backend.services import nebius_client, timetable

# 1x1 transparent PNG.
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(temp_db) -> TestClient:
    return TestClient(create_app())


def model_said(**overrides) -> dict:
    base = {
        "classes": [
            {"day": "Mon", "start": "08:00", "end": "10:00",
             "title": "Algorithms", "code": "WIA1002"},
            {"day": "Wed", "start": "09:00", "end": "11:00",
             "title": "Machine Learning", "code": "WIA3005"},
        ],
        "skipped": [],
        "notes": "5-day grid, times inside blocks",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_a_clean_read_becomes_sorted_busy_blocks() -> None:
    blocks, skipped = timetable.parse_classes(model_said())
    assert [(b.weekday, b.start, b.end) for b in blocks] == [
        (0, "08:00", "10:00"),
        (2, "09:00", "11:00"),
    ]
    assert blocks[0].label == "Algorithms (WIA1002)"
    assert skipped == []


def test_chinese_day_names_are_understood() -> None:
    blocks, _ = timetable.parse_classes(
        {"classes": [{"day": "周二", "start": "14:00", "end": "16:00", "title": "操作系统"}]}
    )
    assert blocks[0].weekday == 1
    assert blocks[0].label == "操作系统"


def test_single_digit_hours_are_normalised() -> None:
    blocks, _ = timetable.parse_classes(
        {"classes": [{"day": "Thursday", "start": "9:00", "end": "11:30", "title": "Lab"}]}
    )
    assert (blocks[0].start, blocks[0].end) == ("09:00", "11:30")


def test_a_class_without_a_readable_time_is_dropped_and_named() -> None:
    blocks, skipped = timetable.parse_classes(
        {"classes": [{"day": "Mon", "start": "", "end": "10:00", "title": "Algorithms"}]}
    )
    assert blocks == []
    assert any("Algorithms" in line and "time" in line for line in skipped)


def test_an_impossible_hour_is_dropped() -> None:
    """A model that writes 25:00 must not create a commitment."""
    blocks, skipped = timetable.parse_classes(
        {"classes": [{"day": "Mon", "start": "25:00", "end": "26:00", "title": "Ghost"}]}
    )
    assert blocks == []
    assert skipped


def test_a_backwards_range_is_dropped() -> None:
    blocks, skipped = timetable.parse_classes(
        {"classes": [{"day": "Tue", "start": "16:00", "end": "14:00", "title": "Wrong"}]}
    )
    assert blocks == []
    assert any("end is not after start" in line for line in skipped)


def test_an_unknown_day_is_dropped() -> None:
    blocks, skipped = timetable.parse_classes(
        {"classes": [{"day": "Funday", "start": "09:00", "end": "10:00", "title": "Nope"}]}
    )
    assert blocks == []
    assert any("day" in line for line in skipped)


def test_the_same_slot_twice_is_counted_once() -> None:
    blocks, _ = timetable.parse_classes(
        {"classes": [
            {"day": "Mon", "start": "08:00", "end": "10:00", "title": "Algorithms"},
            {"day": "Monday", "start": "08:00", "end": "10:00", "title": "Algorithms"},
        ]}
    )
    assert len(blocks) == 1


def test_a_location_is_kept_but_does_not_swallow_the_title() -> None:
    blocks, _ = timetable.parse_classes(
        {"classes": [{"day": "Fri", "start": "10:00", "end": "12:00",
                      "title": "AI Ethics", "location": "BK-3"}]}
    )
    assert blocks[0].label == "AI Ethics @ BK-3"


def test_a_garbage_response_produces_no_blocks_and_no_crash() -> None:
    blocks, skipped = timetable.parse_classes({"classes": "not a list"})
    assert blocks == []
    assert skipped == []


# --------------------------------------------------------------------------
# Image validation
# --------------------------------------------------------------------------


def test_an_unsupported_media_type_is_refused() -> None:
    with pytest.raises(AppError) as caught:
        timetable.extract(TINY_PNG, "application/pdf")
    assert caught.value.code == "unsupported_media_type"
    assert caught.value.status_code == 415


def test_broken_base64_is_refused() -> None:
    with pytest.raises(AppError) as caught:
        timetable.extract("!!!not base64!!!", "image/png")
    assert caught.value.code == "invalid_image"


def test_an_oversized_image_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(timetable, "MAX_IMAGE_BYTES", 8)
    with pytest.raises(AppError) as caught:
        timetable.extract(TINY_PNG, "image/png")
    assert caught.value.code == "image_too_large"


def test_a_data_url_prefix_is_tolerated(monkeypatch) -> None:
    """Streamlit hands us raw base64, but a browser client may send a data URL."""
    seen: dict[str, str] = {}

    def fake_chat(messages, **_kwargs):
        seen["url"] = messages[-1]["content"][1]["image_url"]["url"]
        return model_said(), ModelUsage(model="vl", latency_ms=10)

    monkeypatch.setattr(nebius_client, "chat_json", fake_chat)
    result = timetable.extract(f"data:image/png;base64,{TINY_PNG}", "image/png")
    assert result.busy_blocks
    assert seen["url"].startswith("data:image/png;base64,")
    assert seen["url"].count("base64,") == 1


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def test_import_returns_blocks_without_saving_them(client, monkeypatch) -> None:
    monkeypatch.setattr(
        nebius_client, "chat_json",
        lambda *_a, **_k: (model_said(), ModelUsage(model="vl", latency_ms=9)),
    )
    response = client.post(
        "/schedule/import",
        json={"image_base64": TINY_PNG, "media_type": "image/png"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["busy_blocks"]) == 2
    assert body["notes"]
    # Nothing is saved: the schedule is still empty until the user saves it.
    assert client.get("/schedule").json()["configured"] is False


def test_import_rejects_a_pdf(client) -> None:
    response = client.post(
        "/schedule/import",
        json={"image_base64": TINY_PNG, "media_type": "application/pdf"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_a_model_failure_is_a_502_not_a_crash(client, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise UpstreamError("The model call failed")

    monkeypatch.setattr(nebius_client, "chat_json", boom)
    response = client.post(
        "/schedule/import",
        json={"image_base64": TINY_PNG, "media_type": "image/png"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
