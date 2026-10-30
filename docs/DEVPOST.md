# InboxPilot

**From email information to real-world action.**

An AI agent that reads a real Gmail account, extracts tasks and deadlines with
evidence quoted from the source email, decides whether an invitation fits your
week, and — only after you approve it — creates a real Gmail draft and reads it
back to prove it exists.

---

## Inspiration

The project started with a student mailbox.

Universiti Malaya sends everything by email: assignment deadlines, lab
reschedules, card collection notices, hackathon invitations. None of it is hard
to *read*. All of it is hard to *act on*, because acting requires holding the
whole week in your head at once — this deadline is Friday, that lecture clashes
with this event, this notice can be ignored, that one cannot.

The AI email tools we looked at all end in the same place: a summary. They are
excellent at comprehension. But the output is a second inbox you now have to
read, one more surface to check. The original email was never the bottleneck;
the deciding was.

So the question we set out to answer was narrower and, we thought, more useful:

> What does an email agent look like if it is allowed to *act*, but not allowed
> to act *alone*?

That framing decides almost everything downstream. It means the agent has to
show its work, because a human is about to approve it. It means every claim
needs a citation, because "the model said so" is not something you can check at
a glance. And it means the interesting engineering is not in the prompt — it is
in the boundary between what the model is allowed to decide and what Python
decides.

## What it does

The loop is short and every stage is visible in the UI:

```
Connect -> Understand -> Decide -> Approve -> Act -> Verify
```

**Understand.** Nemotron returns a structured analysis: category, key facts,
action items, deadlines, and an explicit list of what it could *not* determine.
Every fact carries a verbatim quote from the email and a label — `explicit` if
the email says it, `inference` if it was derived, `unknown` if it was guessed at.
The backend checks that each `explicit` quote actually occurs in the body. Quotes
that do not are downgraded or dropped.

**Decide.** For an email that invites you somewhere, the agent answers the
question you actually have: *does this fit my week, and is it worth rearranging
for?* You describe your week once — lectures, shifts, standing meetings — and set
a bar: how good does an event have to be before you skip something else for it?
The verdict is one of six:

| Verdict | When |
|---|---|
| **Go** | no clash, and it is worth the hours |
| **Go anyway** | it clashes, but it clears your bar |
| **Decline** | it clashes, and it does not clear the bar |
| **Skip** | no clash, but it is not worth the hours |
| **Ask for the date** | the email never says when it is |
| **Tell me your week first** | no schedule on file — the agent refuses to guess |

That last row is the one we are most attached to. The honest answer to "should I
attend this?" is sometimes "I cannot know yet", and an agent that never says so
is not trustworthy about the cases where it does.

**Approve, Act, Verify.** The approval screen shows the draft, the recipient, the
evidence behind it, and what the agent still does not know. Approving creates a
Gmail **draft** — never a send. The draft is then read back from Gmail and
compared field by field; the returned draft id is displayed only when subject,
recipient and body all match. There is no send call anywhere in the codebase.

**It reads before you ask.** A background watcher polls the inbox, analyses what
arrives, and puts a card in the corner of your screen — outside the browser,
because that is where you actually are. It is deliberately powerless: it can read
and it can notify. It cannot create a draft, change a label, or spend an approval.
Only urgency 2 and 3 produce a card; promotions, social notifications, bulk
senders and mail you sent yourself are filtered before they cost an inference call.

**One week, one page.** The Digest page answers the other question: what did the
last seven days actually ask of me? One model call over the whole window, cached
in SQLite, free to look at again.

## How we built it

FastAPI backend, Streamlit frontend, SQLite for state, and every answer produced
by a live call to **Nebius Token Factory** — nothing pre-generated, nothing from
a fixture. If Nebius is unreachable the app reports an error instead of inventing
output.

Three NVIDIA Nemotron models, chosen for three different jobs:

| Model | Role | Why |
|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | read one email | highest measured JSON reliability at the latency we can afford |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | decide and draft a reply | multi-step judgement |
| `nvidia/Nemotron-3.5-Super-VL` | read a photo of a timetable | a timetable is a grid; a VLM reads the layout OCR discards |

The architectural rule that shaped the codebase is **the model extracts, Python
decides.** The model is good at reading "the event is on Tuesday 14:00–16:00" and
at scoring "how much is this worth, 1–5, with reasons". It is bad at arithmetic
it cannot verify and worse at policy. So the model produces structured fields and
Python does the rest. The clash test is a half-open interval intersection over
the week's busy blocks $B$ and the event window $E$:

$$
\text{clash} \iff \exists b \in B \text{ and } e \in E \text{ with } \max(b_s, e_s) < \min(b_e, e_e)
$$

Half-open on purpose: a lecture ending at 11:00 does not clash with an event
starting at 11:00. The verdict is then a lookup over four cells
(clash × score ≥ threshold), plus two cases where the arithmetic has no input at
all. That means the same email always produces the same verdict, every verdict
can be shown next to the exact lecture it clashes with, and a hostile email can
at worst inflate a score that is clamped to 5 and printed with its reasons.

The codebase that came out of this is 292 tests against a real mailbox rather
than a mock.

## Challenges we ran into

**The hybrid reasoning model silently returned nothing.** Nemotron 3 writes to
`reasoning_content` before it writes an answer. On a 1200-token budget the whole
budget went to thinking, so the call came back with a 200, `content: null`, and
`finish_reason="length"` — which our code then reported as "malformed JSON",
pointing at the prompt instead of the ceiling. We now send
`chat_template_kwargs: {"enable_thinking": false}` on extraction and leave
thinking on where the reasoning chain *is* the product. Measured on the same
email: **2793 ms → 1088 ms, 140 → 81 tokens**. That is 2.6× faster for identical
extraction quality, and it was only available because we measured the default
instead of trusting it.

**The smaller model was not faster, and it lied.** The obvious assumption —
smaller model, faster answer — did not survive three runs each on the same email:

| Model | Valid JSON | Latency | Output tokens | Fabricated quotes |
|---|---|---|---|---|
| `nemotron-3-super-120b-a12b` | 3/3 | 4.9–5.7 s | 1118–1124 | 0 |
| `NVIDIA-Nemotron-3-Nano-30B-A3B` | 3/3 | 4.9–7.4 s | 1229–1330 | 1 |
| `Nemotron-3_5-Lightning` | 0/3 | — | — | never valid JSON |

The 30B model wrote *longer* output, which cancelled its size advantage, and in
one of three runs it produced a quote that does not appear in the email. For an
agent whose output a human is asked to approve, a fabricated citation is a
correctness bug, not a quality trade-off.

**Prompt instructions are a suggestion; a schema is a constraint.** The hardest
bug in the whole project was the Digest quietly padding itself. Asked to pick out
what needed attention, the model wrote plausible advice — *"Confirm presentation
readiness"*, *"Modify InboxPilot's data access permissions"* — none of which
appeared in any email. We tightened the prompt. It kept doing it. Models are
simply very good at sounding like a helpful assistant, and "do not invent tasks"
is a request, not a boundary. So the model stopped writing task text entirely:
Python builds a numbered list of tasks already extracted from this week's mail,
the model returns ids into that list, and anything that does not resolve is
dropped. Invention became structurally impossible rather than discouraged. The
same reasoning applies to `quiet` — computed from what survived validation,
never read from the model's own flag, or a week can be called quiet after three
invented tasks.

**Gmail's restricted scopes mean strangers cannot log in.** `gmail.readonly` and
`gmail.compose` are restricted scopes: letting external users authorise them
requires Google verification and a CASA security assessment, which takes weeks
and is not available to a hackathon project. So this is deliberately a
single-tenant demo on one mailbox, and the honest limitation is stated in the
README rather than hidden. We also hit a trap that costs a day if you find it
late: a consent screen left in **Testing** issues refresh tokens that expire
after seven days, so a demo that works today can be dead next week. Publishing
the app is the fix — it is a single console action, and it has to be followed by
re-authorising, because a token issued while in Testing keeps its seven-day
expiry even after you publish.

**Approval had to be idempotent, not just gated.** A gate that can be clicked
twice is not a gate. The idempotency row is written *before* the Gmail call with
status `pending`, and the unique key is held across the whole write, so two
concurrent retries cannot both reach Gmail. A leftover `pending` row means "we
tried and did not learn the outcome", which refuses the retry rather than
allowing it.

## Accomplishments that we're proud of

- **The gate holds on real data.** Real Gmail in, real draft created, real draft
  read back and verified — and no code path that sends. `no-reply` senders return
  an empty proposal without even calling the model.
- **2.6× faster, 42% fewer tokens**, from one measured flag rather than a rewrite.
- **A digest that cannot invent.** The padding bug is the one we are least
  embarrassed to describe, because the fix was a design change, not a better
  prompt.
- **The cache is honest.** `GET /analyses/{id}` reads SQLite and never triggers
  inference, so browsing during a demo cannot spend credit. Warm reads went from
  5.29 s to 0.023 s.

## What we learned

**Defaults are not free.** Thinking-on was the default and it cost 2.6× latency
for no benefit on extraction. Every number in this project that we assumed
turned out to be wrong.

**Put the constraint in the data structure, not the prompt.** Any rule that
matters has to be enforced somewhere the model cannot talk its way around —
a verified quote, a resolved id, a unique key, a half-open interval.

**Refusing is a feature.** "Tell me your week first" and "I could not determine
the date" are the answers that make the confident ones worth believing.

**Show the denominator.** The digest prints how many messages were read in full
and how many contributed only a subject line. "Three of these I actually read" is
the difference between a summary and a guess, and the reader deserves to know
which one they are looking at.

## What's next

Push notifications instead of polling (which needs a public HTTPS endpoint we do
not have — IMAP IDLE would be instant but demands the full-mailbox
`https://mail.google.com/` scope, an enormous privacy downgrade for a few seconds
of latency). Multi-tenant token storage, which is the real blocker for letting
other people use this. Attachment understanding. Calendar write, behind the same
approval gate. And a proper evaluation set, so "the quotes are verified" can
become "the quotes are verified, and here is the rate at which the model gets the
category wrong".

---

*Built for the Nebius x NVIDIA Global AI Hackathon, Track 2: Best Apps and Agents.*

*Source: [github.com/CaoLongyang/inboxpilot](https://github.com/CaoLongyang/inboxpilot) — MIT licensed.*
