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

Two private addresses are named only by placeholder in this repository (owner, 2026-10-21):
- **`<dmbot private>` email:** the `@getdmbot.com` address DMbot's alerts to the team are sent
  **from** (`MAIL_ALERTS_FROM`). It receives nothing DMbot depends on.
- **`<support team private>` email:** the team's private inbox (the Gmail account above). Every
  `@getdmbot.com` address forwards to it, and the owner's alerts (`OWNER_ALERT_EMAIL`) go to the
  owner's private address. Both live only in Cloudflare and the server's `.env`.

## How DMbot uses Resend
Resend does two jobs, and **only** these two:
1. **Sending (SMTP, `smtp.resend.com`):** every email DMbot's bot and website send, and the team's
   replies from Gmail ("Send mail as", step A below). Each uses its own Resend key.
2. **Receiving on `reply.getdmbot.com` only (#1717):** people's emailed replies to a support
   question. Resend hands each one to DMbot's website API as a signed webhook. The main domain
   `getdmbot.com` is never received by Resend: Cloudflare Email Routing takes it.

So in the Resend dashboard (https://resend.com/domains):
| Domain | Sending | Receiving |
|---|---|---|
| `getdmbot.com` | **On** (DKIM and SPF records in Cloudflare) | **Off** (Cloudflare Email Routing has the MX) |
| `reply.getdmbot.com` | Not needed (nothing is sent from it) | **On** (its MX on the name `reply` in Cloudflare) |

That webhook reaches DMbot through the Cloudflare tunnel's route `^/mail/inbound$`. The tunnel passes
only the paths listed in docs/DEPLOY.md ("The routes the tunnel has today"), so a mail path that isn't
on that list gets an empty 404 from Cloudflare and never reaches DMbot.

### The whole flow
```
                         ┌──────────────── Resend (sending) ────────────────┐
DMbot bot (core) ──SMTP──┤                                                  ├──► a person's inbox
DMbot website API ─SMTP──┤  From: accounts@ / info@ / <dmbot private> email ├──► <support team private> email
Team, from Gmail ──SMTP──┤                                                  ├──► a person's inbox
                         └──────────────────────────────────────────────────┘

A person writes to any @getdmbot.com address
    ──► Cloudflare Email Routing ──► <support team private> email (the team inbox)

A person replies to a support email (Reply-To: info+ticket-<id>-<token>@reply.getdmbot.com)
    ──► Resend (receiving, reply.getdmbot.com)
    ──► webhook POST https://api.getdmbot.com/mail/inbound (signed, MAIL_INBOUND_SECRET)
    ──► DMbot checks the token and the sender, adds the reply to the ticket
    ──► the support helper answers again, or hands it to the team
```

### What DMbot sends, and why
Every email goes through `core/src/dmbot/mail.py`. A person's email is sent at most once per kind
(the `sent_emails` table records who got which kind and when, never the text), all mail together
stays under `MAIL_DAILY_CAP`, and no address or email text is ever logged.

**To a person (DMs, players, customers):**
| Email | Sent by | From | Why |
|---|---|---|---|
| Sign-in link ("Your DMbot sign-in link") | website | `MAIL_FROM` (accounts@) | Signing in to the website by email. |
| Move code ("Your DMbot move code") | website and bot | `MAIL_FROM` (accounts@) | Moving a campaign or account to another server; one email per code. |
| Recap ready / No recap this time | bot | `MAIL_FROM` (accounts@) | Tells the DM their session recap is ready to read and post (nothing posts until they press Post), or why it couldn't be made. |
| Try It ends soon | bot | `MAIL_FROM` (accounts@) | Offer email: the free trial's last days. Carries an unsubscribe link and the postal address; not sent while `MAIL_POSTAL_ADDRESS` is empty. |
| 30 more days of Try It, free | bot | `MAIL_FROM` (accounts@) | Offer email: a trial extension. Same rules as above. |
| We got your question | website | `MAIL_SUPPORT_FROM` (info@) | The thank-you for a support ticket. |
| Support answer | website | `MAIL_SUPPORT_FROM` (info@) | DMbot's support helper (the AI) answers, or tells the person a person will reply. |
| Team's reply | website | `MAIL_SUPPORT_FROM` (info@) | A reply typed on the admin page. |

Every support email has the Reply-To `info+ticket-<id>-<token>@reply.getdmbot.com`
(`MAIL_REPLY_DOMAIN`), so the person's reply comes back through Resend into the right ticket. The
token proves the ticket, and the reply must come from the address on that person's account;
anything else is dropped and only counted.

**To the team (from the `<dmbot private>` email, `MAIL_ALERTS_FROM`):**
| Email | Goes to | Why |
|---|---|---|
| A DMbot support ticket is waiting | `SUPPORT_TEAM_EMAIL` (support@, forwarded to the `<support team private>` email) | A new ticket: only the DMbot ID and plan, never its words. |
| Needs the team | `SUPPORT_TEAM_EMAIL` | The helper handed a ticket to a person (unhappy, asked for a person, 5 answers, no address, or the AI down for a day). |
| DMbot support this week | `SUPPORT_TEAM_EMAIL` | Monday's counts. Not sent in a week with no questions. |
| Owner alert | `OWNER_ALERT_EMAIL` (the owner's private address) | The AI, Deepgram, Stripe, mail or the database failed. One fixed sentence, at most once an hour per problem and 5 a day each from the bot and the website (section C). |

**Not DMbot's mail:** Stripe sends its own receipts and payment emails from Stripe, and
`admin@getdmbot.com` is only ever a person writing by hand (DMbot refuses it as a sender, #1425).

**When it fails:** the ticket, recap or code is kept (an offer email is tried again next run). A
failure to send tells the owner (an alert email, or a private Discord message when mail itself is
what failed).

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
