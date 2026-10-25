"""Guards around configuration.

These are cheap tests that protect expensive mistakes: a leaked client secret in
a tracked file, a scope creeping beyond read + draft, or a credential file that
is not ignored by git.
"""

from __future__ import annotations

import re

from backend.config import REPO_ROOT, Settings, get_settings


def test_settings_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_MODEL_FAST", "from-env")
    assert Settings(_env_file=None).nebius_model_fast == "from-env"


def test_env_example_contains_no_real_credentials() -> None:
    """The template is tracked by git, so it must never hold a real secret."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "GOCSPX-" not in text, "a Google client secret is in the tracked template"
    assert not re.search(
        r"\d{6,}-[A-Za-z0-9_-]{20,}\.apps\.googleusercontent\.com", text
    ), "a real Google client id is in the tracked template"
    assert not re.search(r"^NEBIUS_API_KEY=\S+", text, re.MULTILINE), (
        "a Nebius API key is in the tracked template"
    )


def test_gitignore_covers_every_credential_file() -> None:
    entries = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for required in (".env", "token.json", "credentials.json", "data/"):
        assert required in entries, f"{required} is not ignored by git"


def test_scopes_stay_read_and_draft_only() -> None:
    scopes = get_settings().google_scope_list

    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.compose" in scopes

    forbidden = ("gmail.modify", "gmail.send", "mail.google.com", "gmail.labels")
    offending = [s for s in scopes if any(f in s for f in forbidden)]
    assert not offending, f"scope crept beyond the approved set: {offending}"


def test_redirect_uri_matches_the_local_callback() -> None:
    assert get_settings().google_redirect_uri.endswith("/auth/google/callback")


def test_unconfigured_settings_report_themselves() -> None:
    empty = Settings(
        _env_file=None,
        nebius_api_key="",
        google_client_id="",
        google_client_secret="",
        demo_refresh_token="",
    )
    assert empty.is_nebius_configured is False
    assert empty.is_google_configured is False
    assert empty.is_demo_account_configured is False
