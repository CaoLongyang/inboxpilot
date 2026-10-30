"""Run the API and the UI as one process, for a single-port host.

Local development uses two terminals (`scripts.serve` plus `streamlit run`)
because you want to restart one without the other. A container has one entry
point and one public port, so both children are started here and the parent
stays alive to supervise them.

Why the API is waited on before the UI starts: the frontend caches a failed
/health probe for 20 seconds. Starting Streamlit first would paint "backend
offline" on the first page load and keep showing it for twenty seconds after
the backend was in fact up — on a cold container start, that is exactly the
first impression a judge gets.

Environment:
    PORT       public port for the UI (default 7860, what Hugging Face Spaces
               expects; Render passes its own)
    API_PORT   internal port for the API (default 8000, never public)
    INBOXPILOT_API_URL is derived from API_PORT and handed to the UI.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PORT = int(os.getenv("PORT", "7860"))
API_PORT = int(os.getenv("API_PORT", "8000"))
API_URL = os.getenv("INBOXPILOT_API_URL", f"http://127.0.0.1:{API_PORT}")

# How long to give uvicorn to answer /health before starting the UI anyway. If
# it never answers, the UI still starts and shows "backend offline", which is a
# better failure than a container that never serves a page.
API_READY_TIMEOUT = 60.0


def _log(message: str) -> None:
    print(f"[start] {message}", flush=True)


def _wait_for_api(proc: subprocess.Popen) -> bool:
    import httpx

    deadline = time.monotonic() + API_READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            response = httpx.get(f"{API_URL}/health", timeout=1.0, trust_env=False)
            if response.status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - not up yet is the expected case
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    env = {**os.environ, "INBOXPILOT_API_URL": API_URL, "PYTHONUNBUFFERED": "1"}

    _log(f"api  -> 127.0.0.1:{API_PORT}")
    api = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--host", "127.0.0.1", "--port", str(API_PORT),
            "--log-level", "info",
        ],
        cwd=REPO_ROOT,
        env=env,
    )

    ready = _wait_for_api(api)
    _log("api ready" if ready else f"api not ready after {API_READY_TIMEOUT:.0f}s, starting the UI anyway")

    _log(f"ui   -> 0.0.0.0:{PORT}")
    ui = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "frontend/app.py",
            "--server.address", "0.0.0.0",
            "--server.port", str(PORT),
            "--server.headless", "true",
            # The platform terminates TLS and proxies to us; Streamlit's own
            # origin checks reject the forwarded host and break websockets.
            "--server.enableCORS", "false",
            "--server.enableXsrfProtection", "false",
        ],
        cwd=REPO_ROOT,
        env=env,
    )

    children = {"api": api, "ui": ui}

    def _shutdown(*_: object) -> None:
        for name, proc in children.items():
            if proc.poll() is None:
                _log(f"stopping {name}")
                proc.terminate()
        for name, proc in children.items():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _log(f"killing {name}")
                proc.kill()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # If either child dies the container is broken; exit so the platform
    # restarts it instead of serving a half-working app.
    while True:
        for name, proc in children.items():
            code = proc.poll()
            if code is not None:
                _log(f"{name} exited with {code}")
                _shutdown()
                return code or 1
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
