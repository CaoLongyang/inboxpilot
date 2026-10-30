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
from backend.schemas import (  # noqa: E402
    ActionKind,
    DraftCreateRequest,
    DraftProposal,
    ProposedAction,
)
from backend.services import drafter, gmail_client, planner, store, token_store  # noqa: E402


def self_test(account: str, *, approve: bool, key: str | None) -> int:
    """Draft a note to yourself. Exercises A11 + A13 without needing an inbox.

    Useful when the mailbox has nothing reply-able in it: the write path and the
    read-back check are the parts most worth seeing work, and neither of them
    needs an inbound email to prove itself.
    """
    if not account:
        print("The connected mailbox has no address on file; cannot self-test.")
        return 2

    proposal = ProposedAction(
        id=uuid.uuid4().hex,
        message_id="self-test",
        kind=ActionKind.CREATE_DRAFT,
        title="Self test: prove the write path",
        why="No inbound email is required to prove that a draft is created and verified.",
        draft=DraftProposal(
            to=[account],
            subject="InboxPilot self test",
            body=(
                "This draft was created by InboxPilot to prove the write path.\n\n"
                "If you can read this in Gmail -> Drafts, creation and read-back both worked."
            ),
        ),
    )
    store.save_proposal(proposal)
    print(f"proposal: {proposal.id}")
    print(f"To      : {account}")
    print(f"Subject : {proposal.draft.subject}")
    print()

    if not approve:
        print("Dry run. Nothing was written to Gmail.")
        print("To create it:  python scripts/approve_one.py --self-test --approve")
        return 0

    key = key or uuid.uuid4().hex
    print(f"idempotency key: {key}")
    try:
        result = drafter.execute(
            DraftCreateRequest(
                proposal_id=proposal.id,
                idempotency_key=key,
                draft=proposal.draft,
                approved_by=account,
                approved=True,
            )
        )
    except AppError as exc:
        print(f"\nFAILED  {exc.code}: {exc.message}")
        return 1

    print()
    print("=" * 72)
    print(f"RESULT  {result.status.value.upper()}")
    print("=" * 72)
    print(f"draft_id : {result.draft_id}")
    print(f"verified : {result.verified}")
    print(f"message  : {result.message}")
    print()
    print("Open Gmail -> Drafts to see it.")
    print()
    print(f"Run again with --key {key} to prove a retry does not duplicate it.")
    return 0 if result.verified else 1


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
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="skip planning: draft a note to yourself, to prove the write path alone",
    )
    args = parser.parse_args()

    state = token_store.status()
    if not state.get("connected"):
        print("Gmail is not connected. Run /auth/google first.")
        return 2
    account = state.get("email") or ""
    print(f"account : {account or '(unknown)'}")

    if args.self_test:
        return self_test(account, approve=args.approve, key=args.key)

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
