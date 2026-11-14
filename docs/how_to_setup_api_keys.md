# How to set up DMbot's API keys

For the owner. One section per paid service. Each section says which keys DMbot needs, what each key
is for, the **least** it should be allowed to do, and its name in the server's `.env` file.

**The rules for every key:**
- **Never paste a key into chat, an issue, a pull request or this repository.** Give it to dev1 in
  dev1's own session. dev1 puts it on the server with `scripts/set-key NAME`, and it never goes
  anywhere else.
- **Least privilege** (CLAUDE.md): give each key only what it needs. A key that only reads costs or
  usage ends in `_READ_ONLY`.
- **One key, one job.** When a service offers it, make a separate key for DMbot rather than reusing
  one of your own. You can then switch it off on its own.
- **Spend limits:** where a service lets you cap monthly spend, set one. DMbot also has its own caps,
  but a cap at the service is the last line of defence.
- The menus below were checked in November 2026. Websites rename their menus now and then. If a
  button has moved, look for the same words nearby, or ask Supervisor on the issue.

**The Costs page** (#3671) shows this month's spend for each service. Where a service has a
read-only cost or usage key, the page reads the figure itself (the `_READ_ONLY` keys below). Where
it doesn't, the page links to the service's own cost page. Anthropic's figure comes from DMbot's own
records (#3672), not a key.

**Quick list of names** (✱ = new, for the Costs page):

| Service | `.env` name | What it is for |
|---|---|---|
| Discord | `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | the bot, and "Sign in with Discord" |
| Anthropic | `ANTHROPIC_API_KEY` | every AI job |
| Google (Gemini) | `GEMINI_API_KEY` | only if the recap writer is moved to Gemini |
| Google (sign-in) | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | "Sign in with Google" on the admin page |
| Deepgram | `DEEPGRAM_API_KEY`, ✱ `DEEPGRAM_API_KEY_READ_ONLY` | speech to text; reading usage and the bill |
| Speechmatics | `SPEECHMATICS_API_KEY` | speech to text (comparison tests) |
| Voyage AI | `VOYAGE_API_KEY` | search by meaning |
| ElevenLabs | `ELEVENLABS_API_KEY`, ✱ `ELEVENLABS_API_KEY_READ_ONLY` | recap audio; reading usage |
| Stripe | `STRIPE_SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET` | payments |
| Resend (email) | `SMTP_APP_PASSWORD`, `MAIL_INBOUND_SECRET` | sending email; reading replies |
| Cloudflare | `TURNSTILE_SECRET_KEY`, `CLOUDFLARE_TUNNEL_TOKEN` | the "are you a person?" check; putting the web API online |
| GitHub | `GITHUB_FEEDBACK_TOKEN` | the website's "Say hello" form |

---

## Anthropic (Claude): the AI

**Website:** https://console.anthropic.com/ (API keys: https://console.anthropic.com/settings/keys,
billing and limits: https://console.anthropic.com/settings/billing)

### `ANTHROPIC_API_KEY` (in use)
- **What for:** every AI job: cleaning transcripts, story reading, recaps, rules answers, support
  replies.
- **How to make it:** Console → **API keys** → **Create key**. Name it `DMbot server`, and put it in
  its own **workspace** (Console → **Workspaces** → **Create workspace**, for example "DMbot"). A
  workspace lets you set a spend limit and see DMbot's costs apart from anything else.
- **Permissions:** an ordinary API key has no permission settings; it can call the models. That is
  all DMbot needs.
- **Limits:** set a **monthly spend limit** on the DMbot workspace (Console → **Workspaces** →
  DMbot → **Limits**). When it is reached, Anthropic refuses further calls and DMbot messages the
  admins (`DMBOT_ADMIN_PRIMARY_ID`, `DMBOT_ADMIN_SECONDARY_ID`) once a day.

### No Admin key (`sk-ant-admin…`)
Anthropic's cost report needs an **Admin key**. An Admin key can also create and delete API keys and
manage your organisation's members, which is far more than a cost page needs. **Don't make one for
DMbot, and never put one on the server.** The Costs page gets Anthropic's figure from DMbot's own
records of each call (#3672). For the bill itself, it links to the console's **Cost** and
**Usage** pages.

---

## Google: Gemini and "Sign in with Google"

**Websites:** Google AI Studio: https://aistudio.google.com/apikey. Google Cloud console:
https://console.cloud.google.com/ (credentials: https://console.cloud.google.com/apis/credentials,
billing: https://console.cloud.google.com/billing).

### `GEMINI_API_KEY` (only if `AI_PROVIDER_CREATIVE=gemini`)
- **What for:** writing recaps with Gemini instead of Claude. It is off unless you turn it on, so
  you don't need this key today.
- **How to make it:** AI Studio → **Get API key** → **Create API key**, in a Google Cloud project
  **with billing switched on**. On the free tier, Google's terms let it use what is sent, and recaps
  hold your players' story.
- **Permissions:** in the Cloud console → **APIs & Services** → **Credentials** → the key →
  **API restrictions** → **Restrict key** → tick only **Generative Language API**. Then the key can
  do nothing else in your Google account.
- **Limits:** Cloud console → **Billing** → **Budgets & alerts** → a monthly budget with an email
  alert.

### `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (the admin page's "Sign in with Google")
- **What for:** signing you in to getdmbot.com/admin. It is not a paid service and has no spend.
- **How to make it:** Cloud console → **APIs & Services** → **Credentials** → **Create
  credentials** → **OAuth client ID** → **Web application**. Under **Authorised redirect URIs**, add
  the web API's address followed by `/admin/auth/google/callback`.
- **Permissions:** sign-in only (the basic `openid` and `email` details). Don't add any other
  Google API to it.

---

## Deepgram: speech to text

**Website:** https://console.deepgram.com/ (API reference for keys:
https://developers.deepgram.com/reference/manage/keys/create)

### `DEEPGRAM_API_KEY` (in use when `TRANSCRIBER=deepgram`)
- **What for:** turning the table's speech into text, live during a session.
- **How to make it:** Console → your project → **API Keys** → **Create a New API Key**. Name it
  `DMbot server`, role **Member**, no expiry.
- **Permissions:** Member is the smallest role the console offers that can transcribe. Never
  **Admin** or **Owner**: those can manage keys and members.

### ✱ `DEEPGRAM_API_KEY_READ_ONLY` (new, for the Costs page)
- **What for:** reading this month's usage and cost, nothing else. It cannot transcribe, make keys
  or change anything.
- **The permissions it needs:** only `usage:read` and `billing:read`. Both only read. The Costs
  page reads Deepgram's bill in dollars, and that takes `billing:read`.
- **How to make it:** sign in as the account's **Owner**. Deepgram gives `billing:read` only to
  Owners and Admins, and a key can only get permissions its maker has. Then Console → your project →
  **API Keys** → **Create a New API Key**, named `DMbot costs (read only)`. If the form lets you
  choose permissions, tick only **`usage:read`** and **`billing:read`**. Never pick the
  **Admin** or **Owner** role for this key.
- **If the form offers only roles** (Member, Admin, Owner), don't make this key for now. The Costs
  page then shows Deepgram as a link to its billing page. Tell Supervisor on #3671, and we'll find
  another way without a key that can manage the account.
- Give it to dev1, who stores it with `scripts/set-key DEEPGRAM_API_KEY_READ_ONLY`. If the Costs
  page then says "Couldn't be read", dev1 checks the key's permissions.

---

## Speechmatics: speech to text (comparison tests)

**Website:** https://portal.speechmatics.com/ (pricing: https://www.speechmatics.com/pricing)

### `SPEECHMATICS_API_KEY` (in use by the speech-to-text comparison tool)
- **What for:** the bake-off that compares Speechmatics with Deepgram (docs/STT_BAKEOFF.md). The
  bot itself doesn't use it.
- **How to make it:** Portal → **API Keys** (under Settings) → **Generate API key**. Name it
  `DMbot bake-off`.
- **Permissions:** Speechmatics keys have no permission settings. Keep it to this one tool.
- **Limits:** Speechmatics bills from prepaid credits (one credit is one US dollar). Keep only a
  small balance, and turn off automatic top-up if you don't want surprises.

### Costs page
There is no read-only usage key to make today. The Costs page shows Speechmatics as a **link** to
the portal's **Usage** and **Billing** pages. If Speechmatics later offers a usage-only key, its
name will be `SPEECHMATICS_API_KEY_READ_ONLY`.

---

## Voyage AI: search by meaning

**Website:** https://dashboard.voyageai.com/ (documentation: https://docs.voyageai.com/)

### `VOYAGE_API_KEY` (used when search by meaning is on)
- **What for:** turning a campaign's own text into numbers, so a search for "ice goddess" finds
  Auril. Turned on by `DMBOT_MEANING_SEARCH`.
- **How to make it:** Dashboard → **API keys** → **Create new secret key**. Name it `DMbot server`.
- **Permissions:** Voyage keys have no permission settings. Use this key for DMbot only.
- **Limits:** set a usage or budget limit in the dashboard's billing settings if it offers one.

### Costs page
Voyage offers no usage key, so the Costs page shows it as a **link** to the dashboard's **Usage**
page. If one appears later, its name will be `VOYAGE_API_KEY_READ_ONLY`.

---

## ElevenLabs: recap audio

**Website:** https://elevenlabs.io/app/settings/api-keys (subscription and usage:
https://elevenlabs.io/app/subscription)

### `ELEVENLABS_API_KEY` (used for recap audio)
- **What for:** reading recaps aloud, with music and sound effects, and the pronunciation list
  for the campaign's names.
- **How to make it:** Settings → **API Keys** → **Create API Key**. Name it `DMbot recap audio`,
  and turn on **Restrict Key**.
- **Permissions:** give **Access** to only these:
  - **Text to Speech**
  - **Music**
  - **Sound Effects**
  - **Pronunciation Dictionaries** (write: DMbot adds the names' spellings)
  - **Voices:** Read

  Everything else: **No Access**.
- **Limits:** the key form has a **character limit**. Set it a little above
  `DMBOT_RECAP_AUDIO_MAX_CREDITS` times the recaps you expect in a month.

### ✱ `ELEVENLABS_API_KEY_READ_ONLY` (new, for the Costs page)
- **What for:** reading the subscription and this month's usage, nothing else.
- **How to make it:** **Create API Key** again, named `DMbot costs (read only)`, with **Restrict
  Key** on.
- **Permissions:** only **User: Read**. **No Access** to everything else, including Text to Speech.

(You wrote `ELEVEN_LABS_API_KEY_READ_ONLY`. The existing key is spelled `ELEVENLABS_API_KEY`, so the
new one is `ELEVENLABS_API_KEY_READ_ONLY` to match, and the two sit together in `.env`.)

---

## Stripe: payments

**Website:** https://dashboard.stripe.com/ (API keys: https://dashboard.stripe.com/apikeys, webhooks:
https://dashboard.stripe.com/webhooks)

### `STRIPE_SECRET_KEY` (used when `PAYMENT_PROVIDER=stripe`)
- **What for:** opening a checkout, the customer's billing page, and reading and changing
  subscriptions.
- **Use a restricted key** (it starts with `rk_`), not the full secret key (`sk_`): Developers →
  **API keys** → **Create restricted key**, named `DMbot server`.
- **Permissions:** what DMbot calls today (`core/src/dmbot/web/stripe.py`):
  - **Customers:** Write (find, create and update the buyer)
  - **Checkout Sessions:** Write
  - **Customer portal:** Write (the "Manage billing" page)
  - **Subscriptions:** Write (read a plan, cancel or change it)
  - **Prices:** Read

  Everything else: **None**. Make it in **test mode** first and run a test purchase. If a call is
  refused, Stripe's error names the missing permission. Add only that one, then make the live key
  the same way.
- **Costs page:** income comes from DMbot's own payment records, so no extra Stripe key is needed.
  Stripe's fees can be added later with a read-only key, `STRIPE_API_KEY_READ_ONLY` (Balance
  transactions: Read).

### `PAYMENT_WEBHOOK_SECRET`
- **What for:** checking that payment messages really come from Stripe.
- **How to get it:** Developers → **Webhooks** → your DMbot endpoint → **Signing secret** →
  **Reveal**. It starts with `whsec_`. Create the endpoint on the API version named in
  `stripe.py` (`API_VERSION`).

---

## Resend: email

**Website:** https://resend.com/ (API keys: https://resend.com/api-keys)

### `SMTP_APP_PASSWORD` (with `SMTP_HOST=smtp.resend.com`)
- **What for:** sign-in links, move codes, support replies and alert emails.
- **How to make it:** **API Keys** → **Create API Key**, named `DMbot server`.
- **Permissions:** **Sending access** (not Full access), and under **Domain** pick
  **getdmbot.com** only.

### `MAIL_INBOUND_SECRET`
- **What for:** checking that emailed replies really come from Resend.
- **How to get it:** **Webhooks** → the inbound endpoint → its **signing secret** (starts
  `whsec_`).

---

## Cloudflare: the "are you a person?" check and the tunnel

**Website:** https://dash.cloudflare.com/ (Turnstile is in the left menu; tunnels are under **Zero
Trust** → **Networks** → **Tunnels**, https://one.dash.cloudflare.com/)

### `TURNSTILE_SECRET_KEY`
- **What for:** the check on the website's forms. It is free.
- **How to get it:** **Turnstile** → your getdmbot.com widget → **Settings** → **Secret key**.
  The site key goes in the website's build as `PUBLIC_TURNSTILE_SITE_KEY`.
- **Permissions:** none to set. It can only check a form's answer.

### `CLOUDFLARE_TUNNEL_TOKEN`
- **What for:** putting the website's API online without opening the server to the internet.
- **How to get it:** Zero Trust → **Networks** → **Tunnels** → your tunnel → **Configure** → the
  install command holds the token. dev1 copies only the token, with `scripts/set-key`.
- **Permissions:** none to set. It can only run that one tunnel.
- **Costs page:** a link to the billing page (https://dash.cloudflare.com/?to=/:account/billing).

---

## GitHub: the website's "Say hello" form

**Website:** https://github.com/settings/personal-access-tokens/new (fine-grained tokens)

### `GITHUB_FEEDBACK_TOKEN`
- **What for:** posting a message from the website's form as a GitHub Discussion. It is free.
- **How to make it:** **Fine-grained token**, named `DMbot feedback`, expiry **1 year** (set a
  reminder to renew it). Under **Repository access**, choose **Only select repositories** →
  `smv620/Discord-DMbot`.
- **Permissions:** **Repository permissions** → **Discussions: Read and write**. Nothing else.
  (Metadata: Read is added by GitHub on its own.)

---

## Discord: the bot and "Sign in with Discord"

**Website:** https://discord.com/developers/applications

### `DISCORD_TOKEN`
- **What for:** the bot logging in. It is free.
- **Where:** your app → **Bot** → **Reset Token**. **Careful:** resetting it logs the running bot
  out until dev1 puts the new token in. Do this only when dev1 is ready.

### `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET`
- **What for:** "Sign in with Discord" on the website.
- **Where:** your app → **OAuth2** → **Client ID**, and **Client Secret** → **Reset Secret**. Use
  **OAuth2's Reset Secret**, never the Bot page's **Reset Token** (that one is the bot's own key).

---

## The server host, the domain and the rest (links only)

These have bills but no keys in DMbot. The Costs page shows them as links with a short description:
- **The server (VPS):** your hosting provider's billing page.
- **The domain (getdmbot.com):** your registrar's billing page.
- **Cloudflare:** https://dash.cloudflare.com/ (billing, above).

---

## After making a key

1. Open dev1's session and say which key you have, by its name from this page, for example
   "I have ELEVENLABS_API_KEY_READ_ONLY".
2. dev1 runs `scripts/set-key ELEVENLABS_API_KEY_READ_ONLY` and you paste the key **only** into
   that prompt on the server.
3. dev1 restarts what reads it, and checks the Costs page shows the figure, or still says "Not
   connected" if something is wrong.
4. To switch a key off, delete it on the service's website. DMbot then shows "Not connected" for
   that service, and nothing else stops working.
