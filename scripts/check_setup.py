"""Day 1 setup check: proves Nebius + Google config before any feature work.

Usage:
    python -m scripts.check_setup

It prints only whether credentials exist, never their values. Nothing is
written anywhere, and the model call is a single cheap request.
"""

from __future__ import annotations

import sys

from backend.config import REPO_ROOT, get_settings
from backend.services import nebius_client


def mask(value: str) -> str:
    if not value:
        return "MISSING"
    if len(value) <= 8:
        return "SET"
    return f"SET (…{value[-4:]})"


def line(label: str, value: str) -> None:
    print(f"  {label:<34} {value}")


def check_env_file() -> None:
    print("\n[1] Environment file")
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        print(f"  .env found at {env_file}")
    else:
        print("  .env NOT found — copy .env.example to .env and fill it in")
    print("  (values below are masked; nothing secret is printed)")


def check_nebius() -> bool:
    print("\n[2] Nebius Token Factory")
    settings = get_settings()
    line("NEBIUS_BASE_URL", settings.nebius_base_url)
    line("NEBIUS_API_KEY", mask(settings.nebius_api_key))
    line("NEBIUS_MODEL_FAST", settings.nebius_model_fast or "MISSING")
    line("NEBIUS_MODEL_REASONING", settings.nebius_model_reasoning or "MISSING")

    if not settings.is_nebius_configured:
        print("  -> skipped: no API key")
        return False

    try:
        models = nebius_client.list_models()
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  -> FAILED to list models: {exc}")
        return False

    nemotron = sorted(m for m in models if "nemotron" in m.lower())
    print(f"  -> {len(models)} models available, {len(nemotron)} Nemotron:")
    for m in nemotron:
        print(f"       {m}")

    for role, model in (("fast", settings.nebius_model_fast),
                        ("reasoning", settings.nebius_model_reasoning)):
        if model and model not in models:
            print(f"  !! configured {role} model is not in the catalogue: {model}")

    try:
        text, usage = nebius_client.chat(
            [
                {"role": "system", "content": 'Reply with JSON only: {"ok": true}'},
                {"role": "user", "content": "ping"},
            ],
            role="fast",
            max_tokens=32,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  -> FAILED inference call: {exc}")
        return False

    print(f"  -> inference OK: model={usage.model} latency={usage.latency_ms}ms "
          f"tokens={usage.total_tokens}")
    print(f"     raw reply: {text[:120]!r}")
    return True


def check_google() -> bool:
    print("\n[3] Google / Gmail OAuth")
    settings = get_settings()
    line("GOOGLE_CLIENT_ID", mask(settings.google_client_id))
    line("GOOGLE_CLIENT_SECRET", mask(settings.google_client_secret))
    line("GOOGLE_REDIRECT_URI", settings.google_redirect_uri)
    line("GOOGLE_SCOPES", " ".join(settings.google_scope_list) or "MISSING")
    line("DEMO_REFRESH_TOKEN", mask(settings.demo_refresh_token))

    if not settings.is_google_configured:
        print("  -> skipped: OAuth client is not configured")
        return False

    if not settings.google_client_id.endswith(".apps.googleusercontent.com"):
        print("  !! client id does not look like a Google OAuth client id")
    if not settings.google_redirect_uri.startswith(("http://localhost", "https://")):
        print("  !! redirect URI should be http://localhost:... or https://...")

    print("  -> client configured. Interactive consent still has to be run once:")
    print("     GET /auth/google  (task A3)")
    return True


def main() -> int:
    print("InboxPilot — Day 1 setup check")
    check_env_file()
    nebius_ok = check_nebius()
    google_ok = check_google()

    print("\n[4] Summary")
    print(f"  Nebius : {'READY' if nebius_ok else 'NOT READY'}")
    print(f"  Gmail  : {'CONFIGURED (consent pending)' if google_ok else 'NOT READY'}")
    if nebius_ok and google_ok:
        print("\n  Day 1 technical validation: PASS")
        return 0
    print("\n  Day 1 technical validation: INCOMPLETE")
    return 1


if __name__ == "__main__":
    sys.exit(main())
