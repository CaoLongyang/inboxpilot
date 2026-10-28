"""Verify the Nebius Token Factory credentials and model IDs (task A2).

Run this the moment the API key lands, before touching the app:

    python scripts/check_nebius.py
    python scripts/check_nebius.py --list          # dump every model id

It answers three questions in order, and stops at the first one that fails:

1. Is a key configured at all?
2. Does the API accept it?
3. Do the model IDs in `.env` actually exist in the catalogue?

Step 3 is the one that bites. Model IDs drift, and a wrong ID produces a 404
buried inside a 502 at the first `/analyze`, which looks like "the app is
broken" rather than "the config is stale". Here it is one line of output.

Exit code is 0 only if everything passes, so it can gate a release.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python scripts/check_nebius.py`
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.errors import AppError  # noqa: E402
from backend.services import nebius_client  # noqa: E402

OK = "OK"
FAIL = "FAIL"
SKIP = "SKIP"


def _mask(key: str) -> str:
    if not key:
        return "<EMPTY>"
    return f"set (len={len(key)}, starts with {key[:4]}...)"


def _report(step: str, status: str, detail: str = "") -> None:
    line = f"[{step}] {status}"
    if detail:
        line += f"  {detail}"
    print(line)


def _suggest(bad: str, catalogue: list[str]) -> list[str]:
    """Closest real IDs, so the fix is a copy-paste rather than a guess."""
    matches = difflib.get_close_matches(bad, catalogue, n=3, cutoff=0.4)
    if matches:
        return matches
    # Fall back to anything sharing a distinctive word with the bad ID.
    words = [w for w in bad.replace("/", "-").split("-") if len(w) > 3]
    scored = [
        m for m in catalogue if any(w.lower() in m.lower() for w in words)
    ]
    return scored[:3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Nebius credentials and models.")
    parser.add_argument("--list", action="store_true", help="print every model id and exit")
    parser.add_argument(
        "--no-chat", action="store_true", help="skip the live completion (saves credit)"
    )
    args = parser.parse_args()

    settings = get_settings()
    print("Nebius Token Factory check")
    print(f"  base_url : {settings.nebius_base_url}")
    print(f"  api key  : {_mask(settings.nebius_api_key)}")
    print(f"  fast     : {settings.nebius_model_fast or '<EMPTY>'}")
    print(f"  reasoning: {settings.nebius_model_reasoning or '<EMPTY>'}")
    print()

    if not settings.nebius_api_key:
        _report("1/3 key configured", FAIL, "NEBIUS_API_KEY is empty")
        print("\nFix: open .env and set NEBIUS_API_KEY=<the key from your email>")
        print("     .env is git-ignored; never put the key in .env.example.")
        return 1
    _report("1/3 key configured", OK)

    try:
        catalogue = nebius_client.list_models()
    except AppError as exc:
        _report("2/3 api reachable", FAIL, exc.message)
        reason = str(exc.details.get("reason", ""))
        if "401" in reason or "403" in reason or "unauthor" in reason.lower():
            print("\nThe key was rejected. Common causes:")
            print("  - the key was copied with a trailing space or a newline")
            print("  - it belongs to a different project / was already rotated")
            print("  - the account has not been granted Token Factory access yet")
        elif "connect" in reason.lower() or "timeout" in reason.lower():
            print("\nThe API could not be reached. Check the network and")
            print("NEBIUS_BASE_URL (it must end with /v1/).")
        return 1
    _report("2/3 api reachable", OK, f"{len(catalogue)} models")

    if args.list:
        for model_id in sorted(catalogue):
            print(f"  {model_id}")
        return 0

    # ---- step 3: the configured IDs must exist verbatim ----
    wanted = {
        "NEBIUS_MODEL_FAST": settings.nebius_model_fast,
        "NEBIUS_MODEL_REASONING": settings.nebius_model_reasoning,
    }
    missing = {name: value for name, value in wanted.items() if value not in catalogue}
    for name, value in wanted.items():
        status = OK if value in catalogue else FAIL
        _report(f"3/3 {name}", status, value or "<EMPTY>")

    if missing:
        nemotron = sorted(m for m in catalogue if "nemotron" in m.lower())
        print("\nModel IDs are case-sensitive and must match exactly.")
        for name, value in missing.items():
            suggestions = _suggest(value, catalogue)
            if suggestions:
                print(f"\n  {name} = {value}")
                print("  did you mean:")
                for candidate in suggestions:
                    print(f"    {candidate}")
        if nemotron:
            print(f"\n  Nemotron models actually available ({len(nemotron)}):")
            for model_id in nemotron:
                print(f"    {model_id}")
        else:
            print("\n  No model id contains 'nemotron'. Check the catalogue with --list.")
        print("\nUpdate .env, then run this script again.")
        return 1

    if args.no_chat:
        _report("live chat", SKIP, "--no-chat")
        return 0

    # ---- step 4: one real, tiny call, so latency and tokens are measured ----
    try:
        text, usage = nebius_client.chat(
            [
                {"role": "system", "content": "Reply with exactly one word."},
                {"role": "user", "content": "Say OK."},
            ],
            role="fast",
            max_tokens=16,
        )
    except AppError as exc:
        _report("live chat", FAIL, exc.message)
        print(f"\n  details: {exc.details}")
        return 1

    _report("live chat", OK, f"{usage.latency_ms} ms")
    print(f"        reply      : {text[:60]!r}")
    print(f"        tokens     : prompt={usage.prompt_tokens} "
          f"completion={usage.completion_tokens} total={usage.total_tokens}")
    print("\nAll checks passed. /analyze can now run against the real model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
