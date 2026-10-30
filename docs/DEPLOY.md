# Deploying InboxPilot

## Why not GitHub Pages

GitHub Pages serves **pre-built files** from a CDN. It has no process, no
runtime, and no outbound network. InboxPilot cannot run there, and this is not
a configuration we can change:

| What the app does | What it needs | GitHub Pages |
|---|---|---|
| Reads a mailbox | a server holding a Gmail refresh token, calling the Gmail API | no runtime |
| Analyzes an email | an HTTPS call to Nebius Token Factory | no outbound network |
| Remembers your week | SQLite on disk | no filesystem |
| Creates a draft | a server-side write after approval | no server |

What *is* available is the workflow you wanted: **push to GitHub, the site
updates itself, no local processes**. Both hosts below build directly from the
public GitHub repository, so `git push` remains the only deploy step.

## What the container is

One image runs both halves:

- `uvicorn` on `127.0.0.1:8000` — the FastAPI backend, never exposed
- Streamlit on `0.0.0.0:$PORT` — the only public port

`scripts/start_all.py` supervises them, waits for the API to answer `/health`
before starting the UI (otherwise the first page load paints "backend offline"
and the frontend caches that for 20 seconds), and exits if either child dies so
the platform restarts the container.

Because the browser only ever talks to Streamlit, the API needs no public URL
and no CORS entry.

## Environment variables

Set these on the host. Only the first two are required.

| Variable | Value | Why |
|---|---|---|
| `NEBIUS_API_KEY` | your Token Factory key | every analysis call |
| `DEMO_REFRESH_TOKEN` | the Gmail refresh token | lets the demo read the mailbox without a login |
| `DEMO_ACCOUNT_EMAIL` | e.g. `flare8363@gmail.com` | shown in the UI, proves which mailbox is connected |
| `NEBIUS_MODEL_FAST` | `nvidia/nemotron-3-super-120b-a12b` | classification and extraction |
| `NEBIUS_MODEL_REASONING` | `nvidia/Nemotron-3-Ultra-550b-a55b` | WhatIf reasoning |
| `NEBIUS_MODEL_VISION` | `nvidia/Nemotron-3.5-Super-VL` | timetable photo import |
| `APP_ENV` | `production` | stops local-only behaviour |

`NEBIUS_MODEL_FAST` and `NEBIUS_MODEL_REASONING` have **empty defaults in
`backend/config.py`** — the values live in `.env` locally, which is not
committed. A deployment that forgets them starts fine and then fails on the
first analysis, so set them.

The demo token is read from `DEMO_REFRESH_TOKEN` *before* the local token file,
so a redeploy or a restart cannot lose the judge-facing credential.

## Path A — Hugging Face Spaces (recommended)

Free, one public URL, and it sleeps only after 48 hours idle, so a judge
clicking the link almost never waits for a cold start.

1. Create a Space at <https://huggingface.co/new-space> → SDK **Docker** →
   blank template. Note the name, e.g. `CaoLongyang/inboxpilot`.
2. In the Space's **Settings → Variables and secrets**, add the variables
   above as **secrets** (they are injected as environment variables at
   runtime).
3. Point this repository at the Space and push:

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/inboxpilot
   git push space main
   ```

4. The Space needs YAML front matter at the top of `README.md` to know it is a
   Docker app. If the push replaced it, put this back:

   ```yaml
   ---
   title: InboxPilot
   sdk: docker
   app_port: 7860
   ---
   ```

5. Watch the build log. First build is ~3–5 minutes (mostly `pip install`).

## Path B — Render

More familiar flow, but the free tier sleeps after 15 minutes idle, so the
first visit can take ~50 seconds to wake.

1. <https://render.com> → **New → Web Service** → connect the GitHub repo.
2. Runtime **Docker**. Render detects the `Dockerfile`.
3. Add the environment variables from the table above.
4. Deploy. Render sets `PORT` itself; `start_all.py` reads it.

## Checking it worked

- `/` returns the InboxPilot page with a green **backend online** badge.
- **Connect** shows the demo address and a **connected** badge.
- **Inbox → Load inbox** returns real messages.
- **My week** loads the saved timetable.
- Open one email, press **Analyze** — this is the end-to-end proof that
  `NEBIUS_API_KEY` and the model names are right.

## Limits worth knowing before you share the link

- **Anyone with the URL can read that mailbox.** The demo runs as a single
  tenant with a single credential. Only share a mailbox you are happy for
  judges to read.
- **Visitors cannot log in with their own Gmail.** The OAuth flow exists
  (`/auth/google`), but `gmail.readonly` is a Google *restricted* scope: an app
  can only let users outside its test-user list authorize it after Google
  verification and a CASA security assessment. While the app is in Testing
  mode, only addresses added as test users can sign in, and their refresh
  tokens expire after 7 days. The app is also single-tenant — one token file,
  one schedule profile — so a second visitor authorizing would overwrite the
  demo for everyone. Supporting real multi-user login is a per-user token
  store and per-user data scoping, not a config change.
- **The filesystem is ephemeral.** SQLite and any token file are lost on
  restart. The demo credential survives because it comes from the environment;
  the timetable would need re-entering after a restart.
