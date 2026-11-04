"""0340_readings_recorded_now

The website's readings list keeps only readings whose every speaker is recorded now (#3462).
"""

from __future__ import annotations

import importlib

from dmbot.migrations._shared import _swap

# `dmbot_web_readings` (0237, 0303) listed and counted every waiting reading that had a speaker, so
# it showed a reading built from the words of someone who has since stopped being recorded, which
# Discord's list and `ClaimStore.readings_waiting` leave out (`speaker_ids <@ opted_in`). The
# consent rule is re-checked at every step (CLAUDE.md "Consent"): the list and count now keep a
# reading only when every one of its speakers has a consent row in this server, as the transcript
# doors read it. The bot's `opted_in` also drops anyone off the record for now (a pause kept in the
# bot's memory, not in the database), which a door cannot see: the website can show a reading for
# a moment longer than Discord in that one case, never one whose speaker has stopped. The door is
# rebuilt from 0303's text by guarded replacement (a drift stops the migration), with its pinned
# search path and REVOKE kept; no row is changed.
_OLD = importlib.import_module("dmbot.migrations.0303_readings_liar")

_RECORDED = "AND k.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n WHERE n.guild_id = g)"

READINGS_RECORDED_NOW = _swap(
    _OLD.READINGS_LIAR,
    (
        (
            "                        AND cardinality(k.speaker_ids) > 0\n"
            "                        AND k.reason <> 'waiting for its name to be proposed'),\n"
            "            'readings'",
            "                        AND cardinality(k.speaker_ids) > 0\n"
            f"                        {_RECORDED}\n"
            "                        AND k.reason <> 'waiting for its name to be proposed'),\n"
            "            'readings'",
        ),
        (
            "                        AND cardinality(k.speaker_ids) > 0\n"
            "                        AND k.reason <> 'waiting for its name to be proposed'\n"
            "                      ORDER BY",
            "                        AND cardinality(k.speaker_ids) > 0\n"
            f"                        {_RECORDED}\n"
            "                        AND k.reason <> 'waiting for its name to be proposed'\n"
            "                      ORDER BY",
        ),
    ),
)

SQL = READINGS_RECORDED_NOW
