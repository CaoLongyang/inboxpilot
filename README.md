# InboxPilot

**From email information to real-world action.**

InboxPilot is an AI email-to-action agent. It connects to a real Gmail account,
reads real messages, extracts tasks and deadlines with evidence from the source
email, proposes a next step, and — only after the user approves it — creates a
real Gmail draft and verifies that it exists.

Built for the **Nebius x NVIDIA Global AI Hackathon**, Track 2: *Best Apps and
Agents*.

> Status: the full loop runs end to end on a real mailbox — real Gmail in, real
> Nemotron analysis out, real Gmail draft created and read back. 292 tests.
> Nothing in this repository sends email, and no write can happen without
> explicit user approval.

## What the UI actually is

An inbox list, not a wizard. Ten emails load at once with enough of each message
to decide about it, and every row carries its own **Analyze** button, so one
email or ten can be analyzed without leaving the list. Results stay attached to
the row that produced them; a second pass never loses the first answer. The
deep-dive page exists for the one email where a reply gets proposed and
approved, because that is where the approval gate lives.

## The one flow that matters

```
Connect -> Understand -> Decide -> Approve -> Act -> Verify
```

1. **Connect** — Gmail OAuth, minimal scopes (`gmail.readonly` + `gmail.compose`).
2. **Understand** — Nemotron returns a structured `EmailAnalysis`; every claim
   carries a verbatim quote and is labelled `explicit`, `inference` or `unknown`.
3. **Decide** — the agent proposes actions and drafts, and states what it does
   not know. For an invitation (a hackathon, a conference, a webinar) it also
   answers the question the user actually has: *does this fit my week, and is it
   worth rearranging for?*
4. **Approve** — the user edits and approves in the UI. This is a hard gate.
5. **Act** — the backend creates a Gmail **draft** (never a send).
6. **Verify** — the draft is read back from Gmail; the returned draft id is shown.

### The schedule check

Email agents usually stop at "here is what the email says". This one also asks
whether the user can act on it.

The user describes their week once (lectures, shifts, standing meetings) and
sets a bar: *how good does an event have to be before I skip something else for
it?* Then, for any email that invites them somewhere, the verdict is one of:

| Verdict | When |
|---|---|
| **Go** | no clash, and it is worth the hours |
| **Go anyway** | it clashes, but it clears the user's bar |
| **Decline** | it clashes, and it does not clear the bar |
| **Skip** | no clash, but it is not worth the hours |
| **Ask for the date** | the email never says when it is |
| **Tell me your week first** | no schedule on file — we refuse to guess |

The division of labour is deliberate: the model extracts *when* the event is and
scores *how much it is worth* (1-5, with reasons). The overlap arithmetic and
the verdict table are Python (`backend/services/schedule.py`,
`triage.decide`). So every verdict can be shown next to the exact lecture it
clashes with, and a hostile email cannot talk its way into "you must attend" —
at worst it inflates a score that is clamped to 5 and printed with its reasons.

**Filling in the week takes one photo.** Upload a screenshot of a university
timetable and `nvidia/Nemotron-3.5-Super-VL` reads the grid into blocks, which
land in the editor for review before they are saved. Rows it cannot read
(`25:00`, an unknown day) are dropped and named rather than turned into a
commitment, because a misread timetable would quietly corrupt every later
verdict.

### It reads before you ask

Everything above is reactive: the user clicks, the agent answers. A background
watcher (`backend/services/watcher.py`) flips that around. It polls the inbox,
analyses what arrives, and puts a card in the corner of the screen — outside the
browser, because that is where the user actually is.

```
poll inbox ──> filter ──> analyse ──> urgency ──┬─> desktop card (2 and 3)
                                                └─> analysis cache (all)
```

Three decisions make it something you would leave running:

- **Silence by default.** Only urgency 2 and 3 produce a card. Promotions, social
  notifications, bulk senders and mail the user sent themselves are filtered
  before they cost an inference call. A watcher that announces everything is a
  watcher you switch off.
- **The model extracts, Python decides.** `urgency()` is a rule over structured
  fields, not a model opinion, so the same email always produces the same card.
  A notification policy that drifts between runs is not a policy.
- **Deduplication by message id, not by unread state.** Reading a message on a
  phone clears its `UNREAD` label, and marking an old message unread would make
  it look new. The seen table in SQLite is the record, and the first cycle takes
  a silent baseline so a full inbox does not fire twenty cards.

The side effect is the one users notice: because every arriving message is
already analysed, opening one shows the result immediately instead of waiting
for a model round trip. `GET /analyses/{id}` reads the cache and never triggers
inference, so browsing during a demo cannot spend credit.

Polling rather than push is deliberate. Gmail's push notifications need a public
HTTPS endpoint, which this project does not have. IMAP IDLE would be instant
without one, but it requires the full-mailbox `https://mail.google.com/` scope —
an enormous privacy downgrade for a few seconds of latency. One request a
minute costs about 0.6% of the daily Gmail quota.

### One week, one page

The watcher handles *this email*. The **Digest** page answers the other question:
*what did the last seven days actually ask of me?* It is one model call over the
whole window, cached in SQLite, and it costs nothing to look at again.

The naive implementation reads every message in full — a hundred thousand tokens
and a hundred inference calls for a normal week. So the input is built from two
tiers of evidence. Messages that already have an analysis contribute their
summary, tasks and resolved deadlines, which the watcher already paid for.
Everything else contributes only the sender, subject and date that
`messages.list` returned anyway. The page prints how many messages landed in
each tier, because "three of these I actually read, one was only a subject line"
is the difference between a summary and a guess.

The hard part was not summarising. It was the padding. Asked to pick out what
needed attention, the model wrote plausible advice — *"Confirm presentation
readiness"*, *"Modify InboxPilot's data access permissions"* — none of which
appeared in any email. Rewriting the prompt did not stop it; the model is simply
very good at sounding like a helpful assistant. So it no longer writes task text
at all:

```
Python: candidates() -> numbered list of tasks already extracted this week
model : needs_you -> [3, 1]      (ids into that list)
Python: _resolve_task() -> titles, or nothing
```

Invention is now structurally impossible rather than discouraged. An id that
does not resolve is dropped, and an empty list is an accepted answer — the
digest is allowed to say the week was quiet, and `quiet` is computed from what
survived validation rather than read from the model's own flag. Otherwise a week
could be declared quiet after three invented tasks.

Two more choices worth naming. A missing digest returns `digest: null`, not a
404, and generating is a separate explicit act from reading: the page calls
`GET /digest` on every open, and if that quietly ran the model, navigating here
would cost money. And the cache key is *"the last N days"*, not a calendar week,
because a calendar week goes stale at midnight on Monday and would silently show
last week's answer under this week's heading.

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
  routers/             health, auth, emails, analyze, actions, digest
  services/
    nebius_client.py   Token Factory client, thinking on/off, JSON mode
    analyzer.py        email -> structured analysis, quote verification
    gmail_client.py    read messages, build MIME, create/read drafts
    planner.py         decides whether a reply is warranted, drafts it
    drafter.py         the approval gate, idempotency, read-back verify
    schedule.py        interval maths: does this clash with my week?
    triage.py          should I attend this? (verdict table, no model)
    timetable.py       photo of a timetable -> busy blocks
    watcher.py         background poll: filter, analyse, decide, announce
    notify.py          desktop cards (Tk), console fallback
    digest.py          a week of mail -> one page, no invented tasks
    store.py           SQLite: analyses, proposals, execution log, seen mail
    token_store.py     one place that knows where the refresh token lives
frontend/
  app.py               Streamlit UI (inbox list, analysis, digest, approval)
scripts/
  serve.py             run the API (--watch adds the mail watcher)
  watch.py             run the watcher on its own
  start_all.py         API + UI in one process, for a single-port host
  check_nebius.py      verify the key, list reachable models
  bench_models.py      model comparison behind the README table
  analyze_one.py       analyze one real email from the CLI
  approve_one.py       drive the full approve -> draft -> verify path
tests/                 292 contract, gate and reliability tests
docs/
  oauth-checklist.md   Day 1 Gmail OAuth steps + the refresh-token trap
  DEPLOY.md            hosting options, env vars, and why Pages cannot
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

### Run it watching your inbox

```bash
# cards appear in the corner of the screen when mail needs you
python -m scripts.watch

# or start it alongside the API, in one command
python -m scripts.serve --watch
```

`python -m scripts.watch --once --no-notify` runs a single cycle and prints
instead of drawing, which is the quickest way to see what it would say.
`--reset` forgets which messages have been announced, so the current inbox can
be replayed without waiting for new mail.

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

Every answer InboxPilot shows comes from a live call to **Nebius Token Factory**
(`https://api.tokenfactory.nebius.com/v1/`, OpenAI-compatible). Nothing is
pre-generated, cached from a fixture, or produced by a smaller local model —
if Nebius is unreachable, the app reports an error instead of inventing output.

### Where each model is used

| Model | Role | Why this one |
|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | Read one email: category, key facts with quotes, action items, uncertainties | Highest measured JSON reliability at the latency we can afford |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | Decide whether a reply is warranted and write it | Multi-step judgement (who sent it, what is being asked, what we do not know) |
| `nvidia/Nemotron-3.5-Super-VL` | Read a photo of a weekly timetable into busy blocks | A timetable is a grid; a VLM reads the layout that OCR throws away |

All three are NVIDIA Nemotron models served by Nebius. The model IDs are
configuration (`NEBIUS_MODEL_FAST` / `NEBIUS_MODEL_REASONING` /
`NEBIUS_MODEL_VISION`), so the split is visible in `.env.example` rather than
buried in code.

### What we had to measure to make it fast

Nemotron 3 is a **hybrid reasoning** model: it writes to `reasoning_content`
before it writes an answer. For extraction that is wasted work, and on a tight
`max_tokens` budget it is fatal — the whole budget goes to reasoning and
`content` comes back empty with `finish_reason="length"`.

We send `chat_template_kwargs: {"enable_thinking": false}` on the extraction
call and leave thinking on for the reasoning call. Measured on the same email:

| | latency | completion tokens | reasoning chars |
|---|---|---|---|
| thinking on | 2793 ms | 140 | 183 |
| thinking off | **1088 ms** | **81** | 0 |

That is **2.6× faster and 42% fewer tokens** for identical extraction quality.
It is the single largest speed win in the project, and it exists only because
the default behaviour was measured rather than assumed.

### Why the "fast" model is not the smallest one

The obvious assumption — smaller model, faster answer — did not survive
measurement. Same email, same prompt, three runs each (`scripts/bench_models.py`):

| Model | Valid JSON | Latency | Output tokens | Fabricated quotes |
|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | 3/3 | 4.9–5.7 s | 1118–1124 | 0 |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 3/3 | 4.9–7.4 s | 1229–1330 | 1 |
| `nvidia/Nemotron-3_5-Lightning` | 0/3 | — | — | never valid JSON |

The smaller model was not faster and it invented a quote that does not appear
in the email. For an agent whose output a human is asked to approve, a
fabricated citation is a correctness bug, not a quality trade-off, so we pay
for the model that does not hallucinate evidence.

### Guardrails that ride on the model output

- **Citations are verified, not trusted.** Every `EXPLICIT` key fact must carry
  a quote; the backend checks that the quote actually occurs in the email body
  and downgrades or drops the fact when it does not.
- **Truncation is a first-class failure.** `finish_reason="length"` is reported
  as `output_truncated` and retried once with double the token budget, instead
  of surfacing as a confusing "malformed JSON".
- **The model never chooses the recipient.** `To:` is read from the email
  header by the backend. A prompt-injected email cannot redirect a draft.
- **Email content is data, not instructions.** The system prompt says so, and
  the approval gate is the backstop when the model is fooled anyway.

`scripts/check_nebius.py` lists the models a key can actually reach;
`scripts/bench_models.py` reproduces the table above.

### A note for judges running the demo

Google shows an "unverified app" warning before the Gmail consent screen,
because `gmail.readonly` and `gmail.compose` are restricted scopes and
verification is not available to a hackathon project. Click **Advanced →
Go to InboxPilot (unsafe)**. The app requests read-only access plus draft
creation only: there is no send call anywhere in the codebase, and `/health`
reports `email_send_enabled: false`.

## What is intentionally not supported

Sending email, deleting, archiving, forwarding, attachment understanding, and
multi-user enterprise administration.

The background watcher is the one thing that runs without being asked, and it
is deliberately powerless: it reads and it notifies. It cannot create a draft,
change a label, or spend an approval. Every write in this project still goes
through the same gate, whether a human or the watcher noticed the email first.

## License

MIT — see [LICENSE](LICENSE).
