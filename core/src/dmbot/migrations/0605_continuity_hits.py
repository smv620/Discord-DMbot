"""0605_continuity_hits

The website's continuity heads-ups (#3695 half 1, the bot half): on the DM's ask the bot runs the
same check as Discord's continuity card and keeps the heads-ups found for the campaign in
`continuity_hits`; the DM answers them through the queue as the card's buttons do. The page's read
door comes later, in WebDev-lead's migration (Supervisor's ruling on #3695).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `continuity_hits` holds pointers only, never words (GraphEngineer's answer (a) and Supervisor's
# ruling on #3695): the claim, the kind of heads-up, its level, the entry it is about, the earlier
# fact it clashes with and that fact's session (none for a death: the door reads the entry's own
# open ending by `entity_id`, as the card's line does), the character learning a secret (a secret
# clue's line), whether the earlier fact is pinned, and its place in the list. A line in words would
# go stale the moment someone stops being recorded or a fact changes; the read door works the words
# out from these pointers at read time, with consent read then. One set for each campaign, replaced
# whole on each ask (`continuity-ask`), isolated with forced row-level security, its campaign key
# `ON UPDATE CASCADE` (a campaign moved to another server takes it along), not in a backup (it is
# worked out again on the next ask), and swept after a day by the bot.
#
# Two actions go on the queue door, each filed under no name, no target and no flag; the door
# checks only the shapes and the bot checks the rest again (a DM of the campaign, the claim still
# waiting, consent read now). `continuity-ask` carries no text; `continuity-answer` carries the
# claim's id (32 hex), a space and the answer: `c` (the story changed) or `n` (it wasn't real).
# "Ignore" writes nothing anywhere, as on Discord, so it never reaches the queue. The queue door's
# lists, shape check and the table's action list are read back as they are and each anchor must be
# found exactly the number of times given (0604's lists are the ones extended).
ACTIONS = ("continuity-ask", "continuity-answer")
_ID = "[0-9a-f]{32}"
_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (
        "'deed-undo', 'route-length'",
        "'deed-undo', 'continuity-ask', 'continuity-answer', 'route-length'",
        3,
    ),
    # the text's shapes, before the Undo's of World notes
    (
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'continuity-ask' AND clean <> '')\n"
        f"           OR (act = 'continuity-answer' AND clean !~ '^{_ID} [cn]$')\n"
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and two more added at the end.
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_action_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the action list did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(d, '])', ', """
    + _ADDED
    + r"""])');
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;

    CREATE TABLE continuity_hits (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        claim_id        TEXT NOT NULL CHECK (claim_id ~ '^[0-9a-f]{32}$'),
        kind            TEXT NOT NULL CHECK (char_length(kind) BETWEEN 1 AND 40),
        level           TEXT NOT NULL CHECK (char_length(level) BETWEEN 1 AND 20),
        entity_id       TEXT NOT NULL CHECK (entity_id ~ '^[0-9a-f]{32}$'),
        earlier_id      TEXT CHECK (earlier_id ~ '^[0-9a-f]{32}$'),
        earlier_session BIGINT,
        learner_id      TEXT CHECK (learner_id ~ '^[0-9a-f]{32}$'),
        pinned          BOOLEAN NOT NULL,
        position        INTEGER NOT NULL CHECK (position >= 0),
        asked_at        BIGINT NOT NULL CHECK (asked_at > 0),
        PRIMARY KEY (guild_id, campaign_id, claim_id, kind)
    );
    CREATE INDEX continuity_hits_age ON continuity_hits (guild_id, asked_at);
    """
    + _isolate("continuity_hits")
)

ISOLATED_TABLES: tuple[str, ...] = ("continuity_hits",)
