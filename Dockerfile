# InboxPilot in one container: the FastAPI backend and the Streamlit UI.
#
# One image, one public port. The backend is bound to 127.0.0.1 inside the
# container and is never exposed; the browser only ever talks to Streamlit,
# which talks to the API server-side. That is why the demo needs no public API
# URL and no CORS configuration.
#
# The one browser-facing API link is "Authorize Gmail" on the Connect page. It
# is only rendered when no credential is stored, so a deployed demo supplies
# DEMO_REFRESH_TOKEN and never shows it. Without a token that button would
# point at 127.0.0.1 and be unusable from a remote browser.
#
# Runs unprivileged as UID 1000, which is what Hugging Face Spaces expects.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    API_PORT=8000 \
    HOME=/home/app

# Created before any COPY so files can be owned by it at copy time. A recursive
# chown afterwards would duplicate every file into a second layer.
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes.
COPY --chown=app:app requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app frontend/ ./frontend/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app .streamlit/ ./.streamlit/

# The app writes SQLite here, and a token file if someone authorizes
# interactively. On a host with an ephemeral filesystem both are lost on
# restart, which is why the demo credential comes from DEMO_REFRESH_TOKEN.
RUN mkdir -p /app/data && chown app:app /app/data

USER app

EXPOSE 7860

# Streamlit answers /_stcore/health once it is actually serving.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/_stcore/health',timeout=3)"

CMD ["python", "scripts/start_all.py"]
