"""Analyse one real email and print everything, without the web UI.

    python scripts/analyze_one.py                 # newest email in the inbox
    python scripts/analyze_one.py --index 3       # the fourth newest
    python scripts/analyze_one.py --message-id 18f2c...
    python scripts/analyze_one.py --json          # raw response for the README

Why this exists: the Streamlit page is the product, but during development you
want the model's actual output, the evidence audit and the measured latency in
one screen, with nothing cached and nothing rendered. This prints email content
to your terminal, so run it on your own machine only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python scripts/analyze_one.py`
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.errors import AppError  # noqa: E402
from backend.services import analyzer, gmail_client  # noqa: E402


def _pick_email(index: int, message_id: str | None):
    if message_id:
        return gmail_client.get_email(message_id)
    listed = gmail_client.list_emails(max_results=max(index, 1))
    if not listed:
        raise SystemExit("The mailbox is empty, or the credential has no access.")
    return gmail_client.get_email(listed[index - 1].id)


def _print_human(email, analysis, usage, audit) -> None:
    print("=" * 78)
    print(f"SUBJECT : {email.subject or '(no subject)'}")
    print(f"FROM    : {email.sender}")
    print(f"DATE    : {email.date}")
    print(f"ID      : {email.id}")
    print("=" * 78)

    print(f"\nSUMMARY\n  {analysis.summary}")
    print(f"\nCATEGORY\n  {analysis.category.value}  (action required: {analysis.action_required})")

    if analysis.key_facts:
        print("\nKEY FACTS")
        for fact in analysis.key_facts:
            print(f"  [{fact.kind.value}] {fact.text}")
            for ev in fact.evidence:
                print(f"      \"{ev.quote}\"")

    if analysis.action_items:
        print("\nACTION ITEMS")
        for i, item in enumerate(analysis.action_items, 1):
            print(f"  {i}. {item.title}  [{item.kind.value}]")
            print(f"     why : {item.why}")
            if item.deadline:
                when = item.deadline.date or item.deadline.text or "unknown"
                print(f"     when: {when}  [{item.deadline.kind.value}]")
            if item.risk:
                print(f"     risk: {item.risk}")
            if item.needs_user_input:
                print(f"     needs: {', '.join(item.needs_user_input)}")
            for ev in item.evidence:
                print(f"     \"{ev.quote}\"")

    if analysis.uncertainties:
        print("\nUNCERTAINTIES")
        for note in analysis.uncertainties:
            print(f"  - {note}")

    print("\nEVIDENCE AUDIT")
    print(f"  quotes checked : {audit.checked}")
    print(f"  quotes rejected: {len(audit.rejected)}")
    print(f"  claims downgraded: {audit.downgraded}")
    for rejected in audit.rejected:
        print(f"    x \"{rejected}\"")

    print("\nMODEL")
    print(f"  name    : {usage.model}")
    print(f"  latency : {usage.latency_ms} ms")
    print(
        f"  tokens  : prompt={usage.prompt_tokens} "
        f"completion={usage.completion_tokens} total={usage.total_tokens}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse one real email.")
    parser.add_argument("--index", type=int, default=1, help="1 = newest email")
    parser.add_argument("--message-id", help="analyse this Gmail message id instead")
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    settings = get_settings()
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is empty. Run scripts/check_nebius.py first.")
        return 1

    try:
        email = _pick_email(args.index, args.message_id)
        analysis, usage, audit = analyzer.analyse(email)
    except AppError as exc:
        print(f"FAILED [{exc.code}] {exc.message}")
        if exc.details:
            print(f"  details: {exc.details}")
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "analysis": analysis.model_dump(mode="json"),
                    "usage": usage.model_dump(mode="json"),
                    "evidence_audit": {
                        "checked": audit.checked,
                        "rejected": audit.rejected,
                        "downgraded": audit.downgraded,
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    _print_human(email, analysis, usage, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
