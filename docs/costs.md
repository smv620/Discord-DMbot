# What a table costs DMbot to serve

Made by `python -m dmbot.devtools.replay --measure` on 2026-10-10 (#945). It needs no key and no network. Run it again after a price or a feature changes.

## Cost per table-hour

Dollars for one hour of a table, by how much speech is sent to speech-to-text in that hour. The three cases are the 36, 48 and 60 speech-minutes an hour that PLAN.md assumed before; no real table has been measured yet (run 8 will). 60 is a ceiling: every minute of the hour spoken. The twin's own sessions are 76% speech (about 46 minutes an hour), which only shows the typical case is not far off. The AI parts use ratios measured on the twin (below), scaled to each case, and everything is priced at the providers' list prices.

| Case | Speech sent (min/hour) | Speech-to-text | Off-topic filter | DM sidebar | Hosting | Total |
|---|---|---|---|---|---|---|
| low | 36 | $0.155 | $0.042 | $0.007 | not included | **$0.203** |
| typical | 48 | $0.206 | $0.056 | $0.007 | not included | **$0.269** |
| high | 60 | $0.258 | $0.070 | $0.007 | not included | **$0.335** |

Hosting is not in the totals: its monthly cost is not something the twin can measure and nobody has given it yet. To add it, divide the server's monthly cost by the table-hours served a month (run with `--hosting-monthly DOLLARS --table-hours-per-month HOURS`); for example, every $10 a month spread over 100 table-hours adds $0.10 to each table-hour. Without hosting a table-hour costs $0.203 to $0.335.

## How to read it

- Speech-to-text is the biggest part, and it is the price times the speech-minutes a real table sends. The twin's recordings are under a minute each, so they confirm *what* is sent (the pieces and the minimum length), not *how much* a real evening sends. That number is an assumption until run 8.
- The AI parts come from measured ratios (calls per speech-minute for the filter, tokens per question for the sidebar) at those assumed amounts.
- Every figure is at list prices, so a discount would only lower it.

## What was measured

| Session | Table time (min) | Speech sent (min) | Lines | Filter calls | Filter tokens in / out | How |
|---|---|---|---|---|---|---|
| DMOnlyAudio.m4a | 0.9 | 0.6 | 8 | 3 | 698 / 18 | recording, cut as core cuts it |
| dm-and-player.m4a | 0.7 | 0.5 | 5 | 1 | 293 / 12 | recording, cut as core cuts it |
| two-voices | 0.9 | 0.7 | 13 | 3 | 717 / 36 | script, timed by its words |
| bakeoff-story | 3.9 | 3.1 | 50 | 11 | 2843 / 150 | script, timed by its words |

- **Speech sent** is what Deepgram bills: the pieces core would send, without those under 0.25 s. The twin sent 4.8 speech-minutes in 6.3 minutes of table time (76%); a real table is assumed to be quieter, see the cases above.
- **Off-topic filter:** 18 calls for 4.8 speech-minutes, so 3.73 calls per speech-minute, scaled to each case. No campaign names were loaded, so no line was spared for naming something; a real campaign asks about slightly fewer.
- **DM sidebar:** the real engine on its 17 table questions: 1.18 AI calls a question (retries included), 862 tokens in and 50 out, times 6 questions an hour. The campaign was a sample with one house rule and no names; a real campaign's names and house rules make the question a little bigger.

## Prices used (list prices, checked)

| What | Price | Source | Checked |
|---|---|---|---|
| Deepgram Nova-3 speech-to-text | $0.0043 per audio minute (billed per second) | https://deepgram.com/pricing | 2026-10-09 |
| claude-haiku-4-5-20251001, input | $1.0 per million input tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-09 |
| claude-haiku-4-5-20251001, output | $5.0 per million output tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-09 |

No discount, credit or volume plan is counted. Core sends each piece of speech as a short file, so the pre-recorded price applies, not the streaming one ($0.0048 a minute).

## Assumptions

- **How much a real table talks:** low 36, typical 48, high 60 speech-minutes sent per table-hour. That is the range PLAN.md assumed before (about 36 to 60); nothing has measured a real table yet.
- **Sidebar use:** 6 questions an hour, a guess.
- **Tokens** are counted by size (about 3.5 characters each), not by the AI service, so they are estimates. The AI is a small part of the total; the speech-to-text is most of it.
- **No AI at all** (checked in the code): the Transcript Cleaner, the after-session name scan and the rules cards. Only four things ask the AI: the off-topic filter, the DM sidebar, the audio check and reading a names file.
- **Not counted:** reading a names file (the DM does it on purpose, now and then), payments, the website, backups, bandwidth.
- **Audio check ceiling:** it asks the AI only while a speaker's audio is breaking up, at most once a minute for each speaker: 60 calls an hour at the very most, about $0.020 an hour for that speaker. Normal play asks for none, so it is left out of the totals.
- **Estimates inside the measure:** the filter's windows are timed by when each line was said (live, by when its text arrived, a little later) and its short answers are counted at 3 tokens a line; it does not model the filter resting after AI failures. The sidebar's answers come from the test cases' prepared replies, so its output tokens are the cases', not a real model's.
- **Hosting** is the server's monthly cost split over the table-hours a month; it is an input, not a measure.

## What run 8 should confirm

1. Speech-minutes sent per table-hour at a real table, from the end-of-session line "Listened X min, sent Y min of speech" (the log). This is the biggest number here.
2. The AI tokens the service really counted, from the usage line in the log, against the estimates above.
3. How many sidebar questions a DM really asks in an hour (once it is on).
4. The server's real monthly cost and the table-hours it serves a month.
