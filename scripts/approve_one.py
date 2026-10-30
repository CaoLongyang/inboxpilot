"""Run the full approve-and-create path against the real mailbox.

This is the Day 4 acceptance check. It deliberately does the whole thing through
the same service functions the HTTP endpoints call, so what it proves is the
product, not a test double.

    python scripts/approve_one.py --index 0              # plan only, create nothing
    python scripts/approve_one.py --index 0 --approve    # create the draft and verify

Without `--approve` this script is read-only apart from the local analysis cache.
With `--approve` it creates one Gmail DRAFT. It never sends.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.errors import AppError  # noqa: E402
from backend.schemas import DraftCreateRequest  # noqa: E402
from backend.services import drafter, gmail_client, planner, store, token_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0, help="which inbox email to use")
    parser.add_argument("--message-id", default=None)
    parser.add_argument(
        "--approve",
        action="store_true",
        help="actually create the Gmail draft (writes to the mailbox)",
    )
    parser.add_argument("--key", default=None, help="reuse a previous idempotency key")
    args = parser.parse_args()

    state = token_store.status()
    if not state.get("connected"):
        print("Gmail is not connected. Run /auth/google first.")
        return 2
    account = state.get("email") or ""
    print(f"account : {account or '(unknown)'}")

    if args.message_id:
        message_id = args.message_id
    else:
        listed = gmail_client.list_emails(max_results=max(args.index + 1, 1))
        if args.index >= len(listed):
            print(f"Only {len(listed)} emails available.")
            return 2
        message_id = listed[args.index].id

    print(f"message : {message_id}")
    print()

    plan = planner.plan(message_id)
    if not plan.actions:
        print("No action proposed for this email.")
        print("(The planning model declined, or the sender cannot be replied to.)")
        return 0

    action = plan.actions[0]
    draft = action.draft
    print("=" * 72)
    print(f"PROPOSAL  {action.title}")
    print("=" * 72)
    print(f"why       : {action.why}")
    print(f"kind      : {action.kind.value}")
    print(f"scopes    : {', '.join(action.requires_scopes) or '(none)'}")
    if action.missing_info:
        print(f"MISSING   : {'; '.join(action.missing_info)}")
    print(f"proposal  : {action.id}")
    print()
    print(f"To      : {', '.join(draft.to)}")
    print(f"Subject : {draft.subject}")
    print(f"Thread  : {draft.thread_id or '(new)'}")
    print("-" * 72)
    print(draft.body)
    print("-" * 72)
    print()

    if action.missing_info:
        print("Refusing to execute: the proposal is missing information.")
        return 1

    if not args.approve:
        print("Dry run. Nothing was written to Gmail.")
        print(f"To create it:  python scripts/approve_one.py --message-id {message_id} --approve")
        return 0

    key = args.key or uuid.uuid4().hex
    print(f"idempotency key: {key}")
    request = DraftCreateRequest(
        proposal_id=action.id,
        idempotency_key=key,
        draft=draft,
        approved_by=account or "local-operator",
        approved=True,
    )

    try:
        result = drafter.execute(request)
    except AppError as exc:
        print(f"\nFAILED  {exc.code}: {exc.message}")
        print(f"        details: {exc.details}")
        return 1

    print()
    print("=" * 72)
    print(f"RESULT  {result.status.value.upper()}")
    print("=" * 72)
    print(f"draft_id : {result.draft_id}")
    print(f"verified : {result.verified}")
    print(f"message  : {result.message}")
    if result.draft_id:
        print()
        print("Open Gmail -> Drafts to see it.")
    print()
    counts = store.stats()
    print(f"database : {counts}")
    return 0 if result.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
