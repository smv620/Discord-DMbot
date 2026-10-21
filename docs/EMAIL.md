# DMbot email setup

What the `@getdmbot.com` addresses are, where mail goes, and how DMbot sends mail. Owner's setup,
recorded 2026-10-15 (#1341). Like everything in this public repository, this file holds no keys,
passwords or private inbox addresses: the team's inbox is called "the team inbox" below.

## Purpose
1. Mail sent to any `@getdmbot.com` address arrives in one team inbox (a Gmail account).
2. The team replies from that inbox as the `@getdmbot.com` address the mail was sent to.
3. DMbot's servers send their own emails (sign-in links, move codes, Try It reminders,
   support notices) from a `@getdmbot.com` address.

## How it is built
| Part | Service |
|---|---|
| Domain and DNS | Cloudflare |
| Mail coming in | Cloudflare Email Routing (free) |
| Mail going out (SMTP and API) | Resend (free tier) |
| Team inbox | Gmail |

## Addresses (all forward to the team inbox)
| Address | Use |
|---|---|
| `admin@getdmbot.com` | Account and service notices (Stripe, Cloudflare, Discord) |
| `info@getdmbot.com` | General questions |
| `support@getdmbot.com` | **The support address** (docs/PLAN.md, "QAbot and support tickets") |
| `accounts@getdmbot.com` | The sender of DMbot's own account emails |
| `feedback@getdmbot.com` | Feedback |

## 1. Done so far (owner, 2026-10-15)
### Mail coming in: Cloudflare Email Routing
- A routing rule for each address above sends it to the team inbox.
- Cloudflare's MX and SPF records are live. Tested: a message from an outside account to
  `feedback@getdmbot.com` arrived.

### Mail going out: Resend
- Resend account made and `getdmbot.com` verified for sending.
- Resend's "Auto configure" added its DKIM record (`resend._domainkey`) and its SPF records (the
  `send` CNAMEs) to Cloudflare DNS.
- **Receiving is off in Resend** on purpose, so it can't clash with Cloudflare Email Routing.
  Leave it off.

## 2. Next steps
### A. Replying from Gmail as a `@getdmbot.com` address (owner)
1. In Resend, make an API key: https://resend.com/api-keys, then **Create API Key**, with
   **Sending access** only.
   Keep it in a password manager. Never paste it in GitHub, chat or this repository.
2. In Gmail: https://mail.google.com/mail/u/0/#settings/accounts, which is **Settings (⚙️)** >
   **See all settings** > **Accounts and Import**.
3. Under **Send mail as**, click **Add another email address**.
4. Name: for example "DMbot Support". Address: for example `support@getdmbot.com`. Leave
   "Treat as an alias" ticked.
5. SMTP server `smtp.resend.com`, port `587`, username `resend`, password: the Resend API key.
   Choose TLS.
6. Gmail sends a confirmation code to that address. It arrives in the same inbox through
   Cloudflare; enter it.
7. Repeat steps 3 to 6 for each address you want to reply from.
8. In **Accounts and Import**, set **When replying to a message** to **Reply from the same address
   the message was sent to**.

### B. DMbot's own emails through Resend (team work, then owner and dev1)
DMbot already sends mail over plain SMTP (`core/src/dmbot/mail.py`; settings in `.env.example`:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `MAIL_FROM`). It sends from the
owner's Gmail today. Moving it to Resend needs **no new library and no SDK**, only different
settings:

| Setting | Value |
|---|---|
| `SMTP_HOST` | `smtp.resend.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `resend` |
| `SMTP_APP_PASSWORD` | a Resend API key (its own key, separate from the Gmail one) |
| `MAIL_FROM` | `DMbot <accounts@getdmbot.com>` |
| `MAIL_DAILY_CAP` | at most Resend's free daily limit (check the Resend dashboard) |

**Not ready yet:** `scripts/set-key` accepts only a 16-letter Google app password for
`SMTP_APP_PASSWORD`, so it refuses a Resend key. The follow-up issue makes it accept either.
Then the owner saves the key with `scripts/set-key` (dev1 never reads `.env`), and dev1 restarts
and sends one test email.

### C. Owner alerts (#1907)
When the AI service is out of credit or refuses its key, Deepgram refuses its key or has not answered
for 10 minutes, Stripe refuses its key, the mail service fails, or the database can't be reached at
start-up, DMbot sends the owner one short email with one fixed sentence and what to check. It sends
at most one per problem per hour and at most 5 a day from each of the bot and the website (10 slots
that `mail.py` keeps back from other mail under `MAIL_DAILY_CAP`, which must be above 10). The counts
are kept in memory, so a restart starts them again. When mail itself is what failed, the owner gets a
private Discord message instead, at most once an hour. **Known limit:** the website has no Discord
login, so a mail failure seen there is only logged; the bot's own failures and every other problem
are told as above. A mistyped address (the mail service refusing one recipient) is not a failure.
The sentences never hold a key, a ticket or a player's words.

| Setting | Value |
|---|---|
| `OWNER_ALERT_EMAIL` | the owner's private address, set in the server's `.env` at the terminal only. Empty: no alert emails, and the log says so once. |
| `MAIL_ALERTS_REPLY_TO` | optional Reply-To on alerts (for example an ops address). Empty: none. |
| `MAIL_ALERTS_FROM` | the From on alerts (the `<dmbot private>` email). Never `admin@getdmbot.com`: DMbot refuses it for every From setting (#1425). |

## Rules
- No key, password or private inbox address goes in this repository, an issue or a chat.
- Each use gets its own Resend key (Gmail replies; DMbot's server), so one can be revoked
  without breaking the other.
- Support mail is private: what people write to `support@` is never posted on GitHub
  (docs/PLAN.md, "QAbot and support tickets").
