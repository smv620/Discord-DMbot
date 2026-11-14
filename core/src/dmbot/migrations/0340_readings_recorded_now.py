"""0340_readings_recorded_now

The website's readings list keeps only readings whose every speaker is recorded now (#3462).
"""

from __future__ import annotations

import importlib

from dmbot.migrations._shared import _swap

# `dmbot_web_readings` (0237, 0303) listed and counted every waiting reading that had a speaker,
# so it showed a reading built from the words of someone who has since stopped being recorded,
# which Discord's list and `ClaimStore.readings_waiting` leave out (`speaker_ids <@ opted_in`). The
# consent rule is re-checked at every step (CLAUDE.md "Consent"): the list and its count now keep
# a reading only when every one of its speakers is recorded now by the bot's own rule
# (`consent._select`): a consent row under the current wording (`terms`) and, while an outside
# company hears the voices, for that company (`outside`). Both arrive as arguments, as for
# `dmbot_recap_check_names`, because the database cannot know them; so the door has a new
# signature and the old one goes. The bot's `opted_in` also drops anyone off the record for the
# moment (a pause kept in the bot's memory, not in the database), which a door cannot see: the
# website can list such a reading for as long as the pause lasts, which Discord already leaves
# out. The door is rebuilt from 0303's text by guarded replacement (a drift stops the migration),
# with its pinned search path and REVOKE; no row is changed.
_OLD = importlib.import_module("dmbot.migrations.0303_readings_liar")

_RECORDED = (
    "AND k.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n\n"
    "                              WHERE n.guild_id = g AND n.terms_version = terms\n"
    "                                AND (outside IS NULL OR n.outside_to = outside))"
)

_BODY = _swap(
    _OLD.READINGS_LIAR,
    (
        (
            "CREATE OR REPLACE FUNCTION dmbot_web_readings(g BIGINT, c TEXT) RETURNS JSONB",
            "CREATE FUNCTION dmbot_web_readings(g BIGINT, c TEXT, terms INT, outside TEXT)\n"
            "    RETURNS JSONB",
        ),
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
        (
            "'ALTER FUNCTION dmbot_web_readings(BIGINT, TEXT)'",
            "'ALTER FUNCTION dmbot_web_readings(BIGINT, TEXT, INT, TEXT)'",
        ),
        (
            "REVOKE ALL ON FUNCTION dmbot_web_readings(BIGINT, TEXT) FROM PUBLIC;",
            "REVOKE ALL ON FUNCTION dmbot_web_readings(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;",
        ),
    ),
)

READINGS_RECORDED_NOW = "    DROP FUNCTION dmbot_web_readings(BIGINT, TEXT);\n" + _BODY

SQL = READINGS_RECORDED_NOW

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_readings", "dmbot_web_readings(BIGINT, TEXT, INT, TEXT)"),
)
