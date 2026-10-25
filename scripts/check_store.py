"""Inspect the local SQLite database (task A9).

Answers the question "where did the analysis go?" without opening a SQL client:

    python scripts/check_store.py
    python scripts/check_store.py --message-id 18f2c...
    python scripts/check_store.py --json

Prints metadata only: ids, counts, model names, latencies, statuses. Email
subjects and bodies are never printed in the list view, because this output is
the kind of thing that ends up pasted into a chat window or a bug report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python scripts/check_store.py`
    sys.path.insert(0, str(REPO_ROOT))

from backend.services import store  # noqa: E402


def _rows(db: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show what InboxPilot has stored.")
    parser.add_argument("--message-id", help="dump the full analysis for one email")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    db = store.db_path()
    if not db.exists():
        print(f"No database yet at {db}")
        print("It is created on the first backend start (or run `--init`).")
        if args.json:
            print(json.dumps({"exists": False, "path": str(db)}))
        return 0

    store.init_db()
    counts = store.stats()

    if args.message_id:
        hit = store.cache_lookup(args.message_id)
        if hit is None:
            print(f"No stored analysis for message {args.message_id}")
            return 1
        analysis, usage = hit
        payload = analysis.model_dump(mode="json")
        if args.json:
            print(json.dumps({"analysis": payload, "usage": usage and usage.model_dump(mode="json")}, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if usage:
                print(f"\nmodel={usage.model} latency_ms={usage.latency_ms} tokens={usage.total_tokens}")
        return 0

    analyses = _rows(
        db,
        "SELECT message_id, thread_id, model, latency_ms, total_tokens,"
        " analyzed_at, refreshed_count FROM analysis_results"
        " ORDER BY analyzed_at DESC LIMIT 10",
    )
    proposals = _rows(
        db,
        "SELECT id, message_id, kind, status, created_at FROM action_proposals"
        " ORDER BY created_at DESC LIMIT 10",
    )
    executions = _rows(
        db,
        "SELECT action_id, status, draft_id, verified, created_at FROM execution_logs"
        " ORDER BY id DESC LIMIT 10",
    )

    if args.json:
        print(
            json.dumps(
                {
                    "path": str(db),
                    "size_bytes": db.stat().st_size,
                    "counts": counts,
                    "analyses": [dict(r) for r in analyses],
                    "proposals": [dict(r) for r in proposals],
                    "executions": [dict(r) for r in executions],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"Database : {db}")
    print(f"Size     : {db.stat().st_size:,} bytes")
    print(
        "Rows     : "
        f"{counts['analysis_results']} analyses, "
        f"{counts['action_proposals']} proposals, "
        f"{counts['execution_logs']} executions"
    )

    print("\nRecent analyses (newest first)")
    if not analyses:
        print("  (none yet - run POST /analyze once)")
    for row in analyses:
        tokens = row["total_tokens"] if row["total_tokens"] is not None else "-"
        latency = row["latency_ms"] if row["latency_ms"] is not None else "-"
        print(
            f"  {row['analyzed_at'][:19]}  {row['message_id']}  "
            f"model={row['model']}  latency={latency}ms  tokens={tokens}"
            + (f"  refreshed x{row['refreshed_count']}" if row["refreshed_count"] else "")
        )

    print("\nProposals")
    if not proposals:
        print("  (none yet - POST /actions/plan)")
    for row in proposals:
        print(
            f"  {row['created_at'][:19]}  {row['id']}  message={row['message_id']}  "
            f"{row['kind']}  status={row['status']}"
        )

    print("\nExecutions")
    if not executions:
        print("  (none yet - POST /actions/draft after approval)")
    for row in executions:
        print(
            f"  {row['created_at'][:19]}  {row['action_id']}  {row['status']}  "
            f"draft={row['draft_id'] or '-'}  verified={bool(row['verified'])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
