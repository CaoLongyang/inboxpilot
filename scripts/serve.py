"""Start the InboxPilot backend without remembering uvicorn flags.

Usage:
    .\\.venv\\Scripts\\python.exe -m scripts.serve
    .\\.venv\\Scripts\\python.exe -m scripts.serve --no-reload
    .\\.venv\\Scripts\\python.exe -m scripts.serve --port 8001

Why this exists: `uvicorn ... --reload` spawns a reloader plus a child process,
and the reloader also watches the whole repository. Editing a test file, a log
or the local memory folder then restarts the server mid-request. This launcher
watches only the source directories and is a single short command to type.

Reload is **on by default** and that default is deliberate. Without it, editing
`backend/` leaves a server running yesterday's routes, and the symptom is a 404
in the UI that looks like a broken feature rather than a stale process. Opt out
with `--no-reload` when you want a stable server during a demo recording.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

import uvicorn

# Work whether this is run as `python -m scripts.serve` (cwd on sys.path) or as
# `python scripts/serve.py` (only scripts/ on sys.path, so `backend` would not
# be importable).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PORT = 8000

# Only restart on real source changes.
RELOAD_EXCLUDES = [
    ".venv/*",
    ".git/*",
    "data/*",
    "docs/*",
    ".workbuddy-ai/*",
    "tests/*",
    "scripts/*",
    "__pycache__/*",
    "*.db",
    "*.log",
]


def _start_watcher(*, desktop: bool) -> None:
    """Run the mail watcher beside the API.

    Started before `uvicorn.run` so it lives in this process rather than in a
    request handler. With --reload that means it lives in the supervisor, which
    is deliberate: editing a file restarts the app, not the watcher, so the
    demo does not lose its baseline every time a line changes.

    Root logging has no handler under uvicorn (uvicorn configures only its own
    loggers), so the watcher's INFO lines would vanish. A basic config here
    makes the cycle summaries visible.
    """
    from backend.services import gmail_client, notify, store, watcher

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    store.init_db()
    if not gmail_client.is_configured():
        print("  watch  : skipped, Gmail is not connected (see the Connect page)")
        return

    notifier = notify.make_notifier(desktop=desktop)
    threading.Thread(
        target=watcher.run_forever,
        kwargs={"notify": notifier.show},
        name="inboxpilot-watcher",
        daemon=True,
    ).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the InboxPilot API.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--reload",
        dest="reload",
        action="store_true",
        default=True,
        help="Restart on source changes (backend/ and frontend/ only). Default.",
    )
    parser.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        help="Do not restart on source changes; use during a recording.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Also watch the inbox in the background and draw desktop cards.",
    )
    parser.add_argument(
        "--no-cards",
        dest="cards",
        action="store_false",
        default=True,
        help="With --watch, print arrivals to the terminal instead of drawing cards.",
    )
    args = parser.parse_args()

    if args.watch:
        _start_watcher(desktop=args.cards)

    print(f"InboxPilot API -> http://{args.host}:{args.port}")
    print(f"  docs   : http://{args.host}:{args.port}/docs")
    print(f"  health : http://{args.host}:{args.port}/health")
    print(f"  reload : {'on (backend/ changes restart it)' if args.reload else 'off'}")
    print(f"  watch  : {'on (polling Gmail in the background)' if args.watch else 'off'}")
    print("  stop   : Ctrl+C\n")

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_excludes=RELOAD_EXCLUDES if args.reload else None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
