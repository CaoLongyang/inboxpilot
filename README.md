# InboxPilot

**From email information to real-world action.**

InboxPilot is an AI email-to-action agent. It connects to a real Gmail account,
reads real messages, extracts tasks and deadlines with evidence from the source
email, proposes a next step, and — only after the user approves it — creates a
real Gmail draft and verifies that it exists.

Built for the **Nebius x NVIDIA Global AI Hackathon**, Track 2: *Best Apps and
Agents*.

> Status: early skeleton. Nothing in this repository sends email, and no write
> operation can happen without explicit user approval.

## The one flow that matters

```
Connect -> Understand -> Decide -> Approve -> Act -> Verify
```

1. **Connect** — Gmail OAuth, minimal scopes (`gmail.readonly` + `gmail.compose`).
2. **Understand** — Nemotron returns a structured `EmailAnalysis`; every claim
   carries a verbatim quote and is labelled `explicit`, `inference` or `unknown`.
3. **Decide** — the agent proposes actions and drafts, and states what it does
   not know.
4. **Approve** — the user edits and approves in the UI. This is a hard gate.
5. **Act** — the backend creates a Gmail **draft** (never a send).
6. **Verify** — the draft is read back from Gmail; the returned draft id is shown.

## Architecture

```
Streamlit UI  ──HTTP──>  FastAPI backend  ──>  Nebius Token Factory (Nemotron)
(frontend/)              (backend/)       ──>  Gmail API (read + create draft)
                                          ──>  SQLite (analyses, proposals, logs)
```

- The model reasons and proposes. The backend decides permissions, validates
  parameters, and performs the write.
- Email bodies are untrusted input. Instructions inside an email cannot widen
  scopes, skip approval, or call a tool.
- `backend/schemas.py` is the single source of truth for every payload exchanged
  between frontend and backend.

## Repository layout

```
backend/
  main.py              FastAPI app, error envelope, request ids
  config.py            all environment variables, one place
  schemas.py           API contract (single source of truth)
  errors.py            unified error model
  routers/             health, auth, emails, analyze, actions
  services/            nebius_client.py, gmail_client.py
frontend/
  app.py               Streamlit UI
tests/                 contract and health tests
docs/
  oauth-checklist.md   Day 1 Gmail OAuth steps + the refresh-token trap
```

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env      # then fill in the values
```

### Run

```bash
# backend
uvicorn backend.main:app --reload --port 8000
# http://localhost:8000/docs

# frontend (second terminal)
streamlit run frontend/app.py
# http://localhost:8501
```

### Test

```bash
pytest -q
```

## Environment variables

See `.env.example`. Names only, no secrets are committed:

| Variable | Purpose |
|---|---|
| `NEBIUS_API_KEY`, `NEBIUS_BASE_URL` | Nebius Token Factory access |
| `NEBIUS_MODEL_FAST` | cheap model for classification / extraction |
| `NEBIUS_MODEL_REASONING` | stronger model for WhatIf reasoning |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Gmail OAuth client |
| `GOOGLE_REDIRECT_URI`, `GOOGLE_SCOPES` | OAuth callback and minimal scopes |
| `DEMO_REFRESH_TOKEN`, `DEMO_ACCOUNT_EMAIL` | pre-authorized demo account for judging |
| `DATABASE_PATH` | SQLite location |
| `ALLOW_EMAIL_SEND` | must stay `false`; sending is out of scope |

## How we use NVIDIA Nemotron on Nebius

_To be completed with measured values once the model IDs are confirmed._

- Runtime inference: every analysis request calls the Nebius Token Factory
  inference API. No pre-generated results are served.
- Model split: a fast Nemotron model handles classification and extraction; a
  reasoning model is used only for multi-step decision analysis.
- What we record per call: model id, latency, token usage, request status.

## What is intentionally not supported

Sending email, deleting, archiving, forwarding, unattended scheduled agents,
attachment understanding, and multi-user enterprise administration.

## License

MIT — see [LICENSE](LICENSE).
