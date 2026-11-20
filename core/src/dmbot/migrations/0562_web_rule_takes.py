"""0562_web_rule_takes

The website's Pick changes on a followed house-rules set and its Undo (#4841 part C, slice C2;
Supervisor's ruling on #4841).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `rules-update-pick` carries `<version> <picks>`: the version the DM was shown and a hex number
# whose bit i says change i of the shown list (rule changes, then relationship definitions, as
# `set_diff` gives them) is kept; at most 50 changes, so at most 13 hex digits. The bot takes the
# update with the store's own `take_update` and, when anything was left out (the follow ends and
# the campaign gets its own copy), `set_sync.keep_as_own`, as Discord's Pick changes does.
# `rules-update-undo` carries the id of the undo handle the pick's answer gave.
#
# `web_rule_takes` is that handle: what Discord keeps in the open message's view (the `Taken` of
# one take: the copy before, the rules added, the kept definitions) so ↩️ Undo can put it back. A
# website answer cannot hold it (the answer's counts are 200 characters), so one row holds it for a
# day: ids, version numbers and the campaign's own followed set, rules only. `user_id` is the DM
# who picked, so the Undo is for that DM only (the queue row of the pick is swept after an hour, so
# it cannot say), and only for the newest take. Rows older than a day are swept by the bot, which
# is how the id goes ("delete my data" is covered by that day, #4148's catalog says so). Forced
# row-level security, the foreign key `ON UPDATE CASCADE` on the campaign, deleted with it, not in
# a backup (a restored campaign has nothing to undo). The table is written and read by the bot
# alone: no web door.
#
# The queue door's lists, shape check and the table's action list are read back as they are and
# each anchor must be found exactly once, so a drift stops the migration.
ACTIONS = ("rules-update-pick", "rules-update-undo")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up'"
)
_NEXT = _PREV + ", 'rules-update-pick', 'rules-update-undo'"

_LAST_SHAPE = "           OR (act = 'party-catch-up' AND clean !~ '^{_ID} {_ID}$')\n"
_LAST_SHAPE = _LAST_SHAPE.replace("{_ID}", "[0-9a-f]{32}")
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'rules-update-pick' AND clean !~ '^[0-9]{1,9} [0-9a-f]{1,13}$')\n"
        + "           OR (act = 'rules-update-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
    (
        f"{_PREV})\n               AND COALESCE(fl, false))",
        f"{_NEXT})\n               AND COALESCE(fl, false))",
        1,
    ),
    (f"{_PREV}) THEN", f"{_NEXT}) THEN", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and one more added at the end.
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
    """
    + r"""
    CREATE TABLE web_rule_takes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        taken       JSONB NOT NULL,
        def_batch   BIGINT,
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX web_rule_takes_age ON web_rule_takes (guild_id, created_at);
    """
    + _isolate("web_rule_takes")
)

ISOLATED_TABLES: tuple[str, ...] = ("web_rule_takes",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
