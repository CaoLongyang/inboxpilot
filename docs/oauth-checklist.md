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

- [ ] Create a Google Cloud project for InboxPilot (not a personal project).
- [ ] Enable **Gmail API**.
- [ ] Set the OAuth consent screen: app name, support email, developer email.
- [ ] Add scopes: `gmail.readonly` + `gmail.compose` only. Do not add
      `gmail.modify`, `gmail.send`, `mail.google.com` or anything broader.
- [ ] Create an OAuth Client ID of type **Web application**.
- [ ] Register redirect URIs:
  - `http://localhost:8000/auth/google/callback` (local)
  - `https://<final-domain>/auth/google/callback` (add before Day 4 deploy)

### 2. Publishing status (the critical part)

- [ ] Add the demo account as a test user while in Testing.
- [ ] Run one successful authorization locally, confirm `messages.list` works.
- [ ] Switch publishing status to **In production**.
- [ ] Confirm what Google actually shows for this app in production while
      unverified (expected: "Google hasn't verified this app" warning, and a
      ~100 new-user cap). Record the exact behaviour in this file.
- [ ] Re-authorize **after** the status change and store the new refresh token.
      A token issued while in Testing keeps the 7-day expiry.
- [ ] Confirm the refresh token still works after 8+ days (put a reminder on
      2026-11-04; if it is dead, we have a problem to solve immediately).

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

## Things to verify in the Google Cloud console, not from memory

- [ ] Which scopes are classified sensitive vs restricted for this app.
- [ ] Whether `In production` is available without completing verification for
      our scope set, and what limits apply.
- [ ] Whether the "unverified app" screen blocks the specific test account.

Record the answers here the moment they are known. Do not build on assumptions.
