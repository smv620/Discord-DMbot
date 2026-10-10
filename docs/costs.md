# What a table costs DMbot to serve

Made by `python -m dmbot.devtools.replay --measure` on 2026-10-10 (#945). It needs no key and no network. Run it again after a price or a feature changes.

## Cost per table-hour

Dollars for one hour of a table, by how much the table talks. Speech-to-text and the AI are measured on the digital twin's sessions and priced at the providers' list prices (below). The three cases scale the twin's speech to a real table: the twin reads its scripts almost without pauses, and real play has more quiet.

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
- **Not counted:** the audio check (it asks the AI only when a speaker's audio is breaking up), reading a names file, the after-session scan, rules cards (no AI), payments, the website, backups, bandwidth.
- **Hosting** is the server's monthly cost split over the table-hours a month; it is an input, not a measure.

## What run 8 should confirm

1. Speech-minutes sent per table-hour at a real table, from the end-of-session line "Listened X min, sent Y min of speech" (the log). This is the biggest number here.
2. The AI tokens the service really counted, from the usage line in the log, against the estimates above.
3. How many sidebar questions a DM really asks in an hour (once it is on).
4. The server's real monthly cost and the table-hours it serves a month.
