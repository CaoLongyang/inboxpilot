# Privacy Policy — InboxPilot

**Last updated: 24 October 2026**

InboxPilot is a hackathon project built for the Nebius x NVIDIA Global AI
Hackathon. This policy explains exactly what the application does with your
email data. It is intentionally short and specific.

## Who we are

InboxPilot is developed by a two-person team. For any privacy question, contact
**24211639@siswa.um.edu.my**.

## What we access, and why

InboxPilot connects to Gmail using Google OAuth 2.0. We request only two scopes:

| Scope | Why it is needed |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Read the messages you choose to analyze |
| `https://www.googleapis.com/auth/gmail.compose` | Create a **draft** after you approve it |

We do not request permission to send email, delete email, modify labels, or
change account settings. InboxPilot never sends email on your behalf.

## How your data is processed

1. When you ask InboxPilot to analyze a message, the message text and its
   metadata are sent to **Nebius Token Factory**, which runs an NVIDIA Nemotron
   open-source model. That service processes the text to produce a structured
   analysis. This means email content leaves our server and is processed by a
   third-party inference provider.
2. The model's output (summary, tasks, deadlines, and verbatim quotes used as
   evidence) is stored in our database so you can review it.
3. If you approve an action, we call the Gmail API to create a draft. The
   resulting draft ID and execution status are stored so the operation can be
   verified and is not repeated by accident.

## What we store

- The structured analysis result, not the full email body.
- Action proposals and their approval state.
- Execution logs (draft id, status, timestamp).
- OAuth refresh tokens, server-side only.

We do not sell data, we do not use it for advertising, and we do not use it to
train models.

## Google API Services User Data Policy

InboxPilot's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

Specifically:

- Data obtained through Gmail scopes is used only to provide the features
  described above.
- It is not transferred to third parties except as necessary to provide those
  features (the inference provider named above), or as required by law.
- It is not used for advertising, and it is not sold.
- Humans do not read your email content, except where you explicitly ask us to
  help during the hackathon demonstration.

## Your controls

- **Disconnect**: you can revoke access at any time from your Google Account
  security settings, or through the application.
- **Delete**: you can ask us to delete stored analyses, proposals, and logs.
- **Refuse**: no write operation happens without your explicit approval.

## Security

- OAuth client secrets and refresh tokens are stored server-side and are never
  sent to the browser.
- Credentials are excluded from the public source repository.
- Email bodies are treated as untrusted input; text inside an email cannot
  change permissions or trigger actions.

## Retention

Data is retained only for the duration of the hackathon judging period and is
deleted on request. The judging period ends on 15 December 2026.

## Changes

If this policy changes, the updated version will be published at this URL with
a new "Last updated" date.

## Source code

The complete source code is public:
https://github.com/CaoLongyang/inboxpilot
