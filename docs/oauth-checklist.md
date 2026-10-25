# Gmail OAuth — Day 1 checklist and the 7-day trap

Owner: A. Complete this before writing Gmail code, because two items have
external latency and cannot be fixed later.

## The trap

A Google Cloud project whose OAuth consent screen is in **Testing** status
issues refresh tokens that **expire after 7 days**.

Timeline if we get this wrong:

- 2026-10-27 authorize the demo account
- 2026-11-03 refresh token expires
- 2026-12-01 judging starts, the demo account is disconnected, judges see errors

There is no workaround at that point: the judges are not going to walk through a
Google consent screen with us.

**Therefore: the consent screen must be in `In production` before we rely on a
long-lived token, and that has to be verified by 2026-10-28 at the latest.**

## Checklist

### 1. Project and API

- [x] Create a Google Cloud project for InboxPilot (not a personal project).
- [x] Enable **Gmail API**.
- [x] Set the OAuth consent screen: app name, support email, developer email.
- [x] Add scopes: `gmail.readonly` + `gmail.compose` only. Do not add
      `gmail.modify`, `gmail.send`, `mail.google.com` or anything broader.
- [x] Create an OAuth Client ID of type **Web application**.
- [x] Register redirect URIs:
  - `http://localhost:8000/auth/google/callback` (local)
  - `https://<final-domain>/auth/google/callback` (add before Day 4 deploy)

Verified 2026-10-24: client id ends in `.apps.googleusercontent.com`, the
authorization URL carries exactly the two scopes above, and a real consent
returned a refresh token that reads `users.getProfile` (974 messages).
Note: the consent URL also carries PKCE `code_challenge` — see section 5.

### 2. Publishing status (the critical part)

- [x] Add the demo account as a test user while in Testing.
- [x] Run one successful authorization locally, confirm `messages.list` works.
- [ ] Switch publishing status to **In production**.
- [ ] Confirm what Google actually shows for this app in production while
      unverified (expected: "Google hasn't verified this app" warning, and a
      ~100 new-user cap). Record the exact behaviour in this file.
- [ ] Re-authorize **after** the status change and store the new refresh token.
      A token issued while in Testing keeps the 7-day expiry.
- [ ] Confirm the refresh token still works after 8+ days (put a reminder on
      2026-11-04; if it is dead, we have a problem to solve immediately).

#### Where the "Publish app" button lives (console UI as of 2026-10)

The old **"OAuth consent screen"** page has been folded into
**Google Auth Platform**. Either path works depending on what the project shows:

- New UI: `APIs & Services` → `Google Auth Platform` → **Audience**.
  The page shows *Publishing status* and a **Publish app** button.
- Old UI: `APIs & Services` → `OAuth consent screen`.
  The page shows *Publishing status* with a **PUBLISH APP** button.

Clicking it raises a "Push to production?" confirmation. Because
`gmail.readonly` and `gmail.compose` are **restricted** scopes, the app stays
*unverified* after publishing — that is expected and acceptable below 100 users.
Users will see "Google hasn't verified this app" and must click
*Advanced → Go to (unsafe)* to continue.

Record below what actually happened, once done:

- Publishing status now: _(pending)_
- Warning shown to a new user: _(pending)_
- Any user cap shown by the console: _(pending)_

### 5. PKCE (found the hard way, 2026-10-24)

`google-auth-oauthlib` 1.5 defaults to `autogenerate_code_verifier=True`. The
authorization URL is built by one `Flow` and the code exchange by another, so
the verifier must be persisted with the `state` or Google answers
`invalid_grant` on every attempt. Fixed in `backend/services/gmail_client.py`
(`_PENDING_STATES` now stores `{issued_at, code_verifier}`); regression test in
`tests/test_gmail_parsing.py::test_pkce_verifier_survives_until_exchange`.

Consequence for deployment: the pending-state store is in-memory, so the OAuth
flow assumes a single process. The judge-facing path uses `DEMO_REFRESH_TOKEN`
and never touches this flow, so this is acceptable for the hackathon.


### 3. Token storage

- [ ] The refresh token lives server-side only: environment variable or a
      file outside the repository with restricted permissions.
- [ ] `.gitignore` covers `.env`, `token.json`, `tokens/`, `credentials.json`.
- [ ] No token, no email body, and no client secret appears in logs.
- [ ] The frontend never receives an access token or refresh token.
- [ ] `/auth/status` reports only booleans.

### 4. Judges must not need OAuth

- [ ] Store the demo account refresh token as `DEMO_REFRESH_TOKEN` in the
      deployed environment.
- [ ] The app opens already connected for the judge; no Google screen.
- [ ] Provide a fallback: if the demo token is invalid, the UI must say so
      clearly instead of showing an empty inbox or a stack trace.
- [ ] Add an uptime check that calls a read-only Gmail endpoint once a day
      between 2026-11-01 and 2026-12-15, so we notice expiry before judges do.

## Answers verified against Google documentation (2026-10-24)

- **Scope category:** `gmail.readonly`, `gmail.compose` and `gmail.modify` are all
  **restricted** scopes. `gmail.compose` is *not* the cheap option — it can send.
  We still ask only for read + compose, never `gmail.modify` or `mail.google.com`.
- **Can we use it unverified?** Yes. Google's own guidance: *"Personal Use apps:
  If the app is for your personal use (fewer than 100 users), you and your
  limited number of users can continue using the app without going through
  verification (users will be allowed to click through 'unverified app' warning
  screens during sign-in)."* Going beyond 100 users would require verification
  plus a CASA security assessment. We stay far below that.
- **Publishing status:** Google requires the production tier project to be
  published as **In production**. Testing status is for development only, and it
  is exactly what triggers the 7-day refresh token expiry.
- **7-day expiry, verbatim:** *"A Google Cloud Platform project with an OAuth
  consent screen configured for an external user type and a publishing status of
  'Testing' is issued a refresh token expiring in 7 days."*
- **Password changes kill the token:** Google invalidates refresh tokens that
  contain Gmail scopes when the account password changes. Do not change the demo
  account password before judging ends (2026-12-15).
- **Token cap:** 100 refresh tokens per Google account per client id. Creating a
  new one silently invalidates the oldest. Do not re-run the consent flow
  repeatedly "just to be safe".

## Things to verify in the Google Cloud console, not from memory

- [ ] Which scopes are classified sensitive vs restricted for this app.
- [ ] Whether `In production` is available without completing verification for
      our scope set, and what limits apply.
- [ ] Whether the "unverified app" screen blocks the specific test account.

Record the answers here the moment they are known. Do not build on assumptions.
