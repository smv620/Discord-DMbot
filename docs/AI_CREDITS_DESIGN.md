# AI credits: the ledger and the estimate (design note, #4005)

Status: step 1 of section 6 (ledger and charging, no limit) is built (`core/src/dmbot/credits.py`, migration 0418); the rest is a design note, not built. The allowances per plan, whether credits can be bought and what
happens at zero are the owner's (#4005, "Owner, what this needs from you"); this note makes
everything else decided, so building waits only for those numbers. Written by CloudDev-lead,
2026-11-10, for Supervisor's review. PLAN, "AI credits", is the rule; this is how.

## 1. What a credit is
**10 credits = $0.01 of DMbot's AI cost; 1,000 credits = $1.** The code already counts money in
millionths of a dollar (`story_plan.micro_usd`: tokens times the price in `ai.PRICE_PER_MILLION`,
rounded up, a cache write at 1.25 and a read at 0.1 since #4055), so **1 credit = 1,000 micro-dollars**
and `credits = ceil(micro / 1000)`. One conversion, in one function (`credits.of_micro`), so a
change of the unit is one line. A model with no checked price is priced high already (`DEFAULT_PRICE`),
so a credit count is never low.

## 2. What is charged, and what never is
Charged: an AI call made **because the DM chose to add or process material** (PLAN). Never charged:
live play (hours pay for it) and the unpaid small calls around it.

| Feature (`ai.Feature`) | Charged? | Why |
|---|---|---|
| `NAMES`, `NAMES_BACKGROUND`, `NAMES_BACKGROUND_READ`, `WHO_IS_IT`, `PLAN_READER`, `NAME_PASS` on an upload or import | **yes** | lore, secret and adventure uploads, imports, the careful decisions |
| `THEATRE` for an **imported** session (made by press) | **yes** | PLAN: a script for an imported session |
| `IMAGE`, `IMAGE_MAP` for a picture **in a document** | **yes** | PLAN: reading pictures in documents |
| `EXTRACTOR`, the claims and names of a **live** session; `RECAP*`, `THEATRE` of a live session | no | that session's recap and script stay in hours |
| `SIDEBAR*`, `FIGHTBOT`, `AUDIO_CHECK`, `TIME_SUGGEST`, table-chat `IMAGE` | no | live (`ai.LIVE_FEATURES` and the table's chat) |
| `SUPPORT`, `SAID_AS`, `SHEET` | no | one small call a person asked for; below the "a few credits" bar |

The same feature can be both (`NAMES` on an upload is charged; the session's own names are not), so
**the charge is named by the job, not by the feature**: the caller says why it is calling.

## 3. The seam: one place that charges
`ai.TierClient.complete` already sees every call's real tokens (`Reply.input_tokens`, `output_tokens`,
`cache_*`) and logs them. Add a `purpose: Charge | None` argument, set by the **job**, never by the
client's creator:

```
Charge(guild_id, campaign_id, owner_user_id, job_kind)   # job_kind: "lore" | "import" | "script" | ...
```

A job opens it with a `contextvars` scope (`async with credits.preparing(campaign, "lore"):`), so the
many call sites inside a lore read (`lore_read`, `traits_read`, `claims`...) are **not edited one by
one**: the client reads the scope, and with none set nothing is charged. A call outside any scope is
free, which is the live-play rule by construction. After each call returns, the client computes
`credits = of_micro(micro_usd(model, in, out, cache_read, cache_write))` and records it. A call that
**fails or is cut** is charged for the tokens the service reports (it charged DMbot), never for the
limit. A retry is charged like any call. The log line gets `credits=N` (a count).

## 4. The ledger
Two tables, both forced-RLS and listed in `campaigns/tables.py` like `plan_spend`:
- `ai_credit_spend (guild_id, campaign_id, id, owner_user_id, job_kind, feature, credits BIGINT, at_ms)`
  one row per call (counts only, no text), kept after a document or upload is removed (the `plan_spend`
  rule: removing and re-adding must not get round it); deleted with the campaign; **not in a backup**
  (like `plan_spend`: a restored campaign starts its month fresh, and says so).
- `owner_credits (owner_user_id, month_start, credits BIGINT, PRIMARY KEY (owner_user_id, month_start))`
  the month's total per **account**, an upsert under a row lock exactly as `owner_hours` is
  (`usage.add_minutes`): two campaigns of one owner charging at once cannot lose a count, and the total it
  returns says where the credits stand. `BIGINT` from the start (#4060: no sum in a 32-bit column).
- **The month** is the plan period the hours use (`hours.Month`, from the plan's renewal), so credits and
  hours reset together and never roll over. **A server on the free list** (`DMBOT_FREE_GUILDS`) is metered
  in `ai_credit_spend` but not added to the owner's `owner_credits` (the rule #3792 gave hours): a paid
  owner's allowance is untouched by a listed server. Accounts on the free list: no limit, still metered.
- The charge writes **as soon as the call returns**, in its own short transaction on the meter
  connection (`db.meter`, as hours), so a crash loses at most one call's count, never a month's.
- The allowance for a plan is one table in `plan_rules` (`CREDITS_PER_MONTH = {plan: credits}`),
  next to the hours; a grant has its level's. Numbers pending the owner.

## 5. The estimate
Every preparing job shows **"About N AI credits. You have M left this month."** with a **Go** button
before it starts, in Discord and on the website, when `N` is over a few credits (`ESTIMATE_BAR`, say
20, a setting). `M = allowance - owner_credits.credits` for the month.
- `N = units x CREDITS_PER_UNIT[job_kind]`, one table of the **measured** costs (dev1's item 1 on #4005:
  per 10,000 words of lore, per hour of an import, per decision, per script, per picture page). Units
  are counted from what the DM gave: words (`words_of`), a session's hours (its lines or length),
  items waiting, pages (`ReadText.pages`, #4000, and `picture_pages`). A cautious count, as
  `story_plan.tokens_in`: an estimate comes out high, never low. The charge is the real cost; the two
  are logged side by side (counts) so the table is tuned from numbers, like #2596's limits.
- The estimate is made by the **same function** for Discord and the website
  (`credits.estimate(job_kind, units) -> Estimate(credits, left)`); the website reads it through a
  SECURITY DEFINER door (`dmbot_web_credits_left`) like the hours, and the account page shows credits
  beside hours (parity, CLAUDE.md: Discord and the website stay level).
- **Never half-run.** The check is at the **start**: a job starts only when `left >= N` (or the owner
  chose another rule for question 3). Once started it **finishes**: if the real cost passes the estimate
  it still runs to the end (the month may then read a little below zero, and shows 0 left), and the next
  job is the one that waits. Nothing stops halfway and no partial result is thrown away.
- **At zero:** the job does not start; the DM is told what it needs (credits) and **when the month
  resets** (`Month.end`), in plain words, in Discord and on the website; live play is untouched.
- **The wait shows it is working** (CLAUDE.md): the estimate card, then the job's own progress (stage,
  part of parts, elapsed, last moved), as the other long jobs do; credits spent so far is a count on it.

## 6. How to build it (small PRs, each safe alone)
1. **Ledger and charging, no limit** (CloudDev): the two tables (one migration), `credits.of_micro`,
   `credits.preparing`, the client's recording, `credits=` in the log. Nothing is refused and nothing
   is shown: it only **measures**, which is also dev1's item 1 on a real run. Tests: the conversion,
   a call outside a scope is free, a call inside is charged exactly its tokens, a failed call is charged,
   a free-list server is metered but does not touch the owner's month, two campaigns charge at once.
2. **The estimate and Go in Discord** (CloudDev) for lore and imports first; the unit table filled
   from the measure.
3. **Credits left on the website: the door, the account page and the estimate** (WebDev-lead, with
   a pattern copy for the assistant), and the same estimate card on the pages that start a job.
4. **Enforcement** (CloudDev), after the owner's numbers: the start check, the "at zero" words, the
   plan table; behind a switch (`DMBOT_CREDITS_ENFORCED`, off) until dev1 has checked a month's numbers.
5. Optional, if the owner says yes to buying: the add-on, as "Extra hours" is.

## 7. Risks and decisions to confirm
- **A call with no scope is free**: a new preparing job that forgets `preparing()` is not charged. A test
  lists every preparing job and fails if its entry point does not open a scope (the way `test_alerts`
  requires every alert to name its problem).
- **Prompt caching (#4054)** lowers the real cost; the ledger charges what the service reports, so
  credits follow the saving (a cache read is a tenth).
- **Estimate vs real** will differ most for lore (the AI's answer length). The first month's logged pairs
  set the unit table; until then the estimate is high on purpose.
- **Backups and moves:** `ai_credit_spend` is campaign data that is not in a backup (said in
  `NOT_IN_BACKUP`), and a campaign move keeps its rows (`ON UPDATE CASCADE`); `owner_credits` is the
  person's, never moves with a campaign (as `owner_hours`).
- **Privacy:** counts only: no text, no names, no file names in the ledger or the log.

## 8. What step 1 does (built)
- `credits.preparing(guild, campaign, job_kind)` is a plain `with` block (not `async with`), like
  `ai_feed.job`. It is opened by `ui.name_background.read_background` (every lore read: Discord's
  button, the website, the queue), `ImportJob.run` (the cleaning of an imported session) and the bot's
  `_extract_import` and `_scan_import` (its story read and name scan). `credits.PREPARING_JOBS` lists
  them and a test fails if one stops opening the scope. Adventure reading, the careful decisions, the
  scripts of imported sessions and picture reads join as their jobs are built or touched.
- `ai.AnthropicClient.complete` calls `credits.record` after every call that answers, a cut one too. A
  call that raises has no token counts from the service, so it is not charged.
- The owner is found once per job (`DMBot._owner_of_campaign`); a campaign nobody owns is recorded under
  owner 0 and never added to a month.
- A failed write is one counts-only warning; the AI work goes on.

