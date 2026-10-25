"""Start the InboxPilot backend without remembering uvicorn flags.

Usage:
    .\\.venv\\Scripts\\python.exe -m scripts.serve
    .\\.venv\\Scripts\\python.exe -m scripts.serve --reload
    .\\.venv\\Scripts\\python.exe -m scripts.serve --port 8001

Why this exists: `uvicorn ... --reload` spawns a reloader plus a child process,
and the reloader also watches the whole repository. Editing a test file, a log
or the local memory folder then restarts the server mid-request. This launcher
watches only the source directories and is a single short command to type.
"""

from __future__ import annotations

import argparse
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the InboxPilot API.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart on source changes (backend/ and frontend/ only)",
    )
    args = parser.parse_args()

    print(f"InboxPilot API -> http://{args.host}:{args.port}")
    print(f"  docs   : http://{args.host}:{args.port}/docs")
    print(f"  health : http://{args.host}:{args.port}/health")
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
