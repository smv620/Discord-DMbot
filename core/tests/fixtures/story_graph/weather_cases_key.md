# Weather cases: key

Invented text for DMbot's tests (#4260 slice 10c-2; Supervisor's ruling A). Every name and line is made up; the four player characters are the sample's, by name only. Files: [weather_cases_lore.txt](weather_cases_lore.txt), [weather_cases_session.txt](weather_cases_session.txt), [weather_cases_key.json](weather_cases_key.json).

The lore gives Frostmere a freezing climate ("frozen town") and Sunreach a hot one ("sweltering port"). Keep both climate rows before the session is read: the note judges only against kept rows. No season is known.

| Line | Case | Heard | Note |
|---|---|---|---|
| 1 | snow in a freezing town | Frostmere: snow | no (it fits) |
| 2 | heat in a freezing town | Frostmere: heat | **yes** |
| 3 | a player's guess | nothing | no |
| 4 | frost in a hot port | Sunreach: frost | no (a desert's winter night; no season known) |
| 5 | snow in a hot port | Sunreach: snow | **yes** |
| 6 | weather that isn't there | nothing | no |
| 7 | a question | nothing | no |
| 8 | rain (never a kind) | nothing | no |
| 9 | a figure of speech, and a place whose name holds "frost" | nothing | no |
| 10 | weather that isn't there ("doesn't snow") | nothing | no |
| 11 | weather to come ("coming next week") | nothing | no |
| 12 | a spell (Scorching Ray) | nothing | no |
| 13 | a figure of speech ("snow-white") | nothing | no |
| 14 | a figure of speech ("the heat of the moment") | nothing | no |
| 15 | a question with no "?" | nothing | no |
| 16 | said as an NPC (Mayor Brask), #4748 | nothing | no |

Line 16: in play, a line the DM says as a named NPC carries that NPC's label, and the weather check never takes it as the DM's word (it may be a lie or a mistake in the story). An imported text has no labels: "DM (as Mayor Brask)" is a speaker of its own, never mapped to the DM, so it gives nothing either way. `tests/test_weather_reading.py` checks the labelled path.

**Once per place** (#4748): a place's note for one kind comes once, and again only when its kept climate changes. Reading this session a second time gives no note; change Sunreach's climate and read it again, and line 5's note comes back.

Scored from the bot's log line: `weather: N heard (lines …)` gives what was heard, and `Weather notes: N heard, M noted` gives the notes. A false note is a note the key doesn't list.
