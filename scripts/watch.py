"""Watch the inbox and report what arrives.

Usage:
    python -m scripts.watch                  # cards, poll every 60s
    python -m scripts.watch --once           # one cycle, then exit
    python -m scripts.watch --interval 30
    python -m scripts.watch --no-notify      # print instead of drawing cards
    python -m scripts.watch --reset          # forget what has been announced

Why a separate command rather than part of the API: the watcher spends most of
its time asleep and occasionally blocks for a second on an inference call. That
is fine in its own process and wrong inside the request handler that serves
/health.

`--reset` exists for the demo. Re-recording a take should not require waiting
for a genuinely new email; clearing the seen table makes the next cycle treat
the current inbox as new again.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services import gmail_client, notify, store, watcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch Gmail and report new mail.")
    parser.add_argument("--interval", type=float, default=watcher.DEFAULT_INTERVAL,
                        help="seconds between polls (default 60)")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--no-notify", action="store_true",
                        help="print to the terminal instead of drawing desktop cards")
    parser.add_argument("--reset", action="store_true",
                        help="forget which messages have been announced, then continue")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    store.init_db()

    if args.reset:
        removed = store.forget_seen()
        print(f"Forgot {removed} previously seen message(s).")

    if not gmail_client.is_configured():
        print(
            "Gmail is not connected, so there is nothing to watch.\n"
            "Authorize the account from the Connect page first, then run this again.",
            file=sys.stderr,
        )
        return 2

    notifier = notify.make_notifier(desktop=not args.no_notify)
    stop = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        print("\nStopping.", flush=True)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    if args.once:
        result = watcher.scan_once(notify=notifier.show)
        print(result.line())
        # Keep the process (and therefore the cards) alive long enough to read.
        notifier.wait(20.0 if not args.no_notify else 0.0)
        notifier.stop()
        return 0

    print(f"Watching the inbox every {args.interval:.0f}s. Ctrl+C to stop.\n")
    try:
        watcher.run_forever(interval=args.interval, notify=notifier.show, stop=stop)
    finally:
        notifier.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
