"""0586_web_reveals

The website's "Things the characters learned" list (#3863 step 2, party knowledge): the bot works
the waiting reveal proposals out in words for one DM (`reveals-ask`), keeps them for the page in
`web_reveals`, and the page reads them through a DM-only door; Keep and Not this go through the
queue to `RevealProposals.keep` / `dismiss`, the Undo through the existing `join-undo`.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The words (who learned what, and how) need the knowledge gate and the consent re-check, which only
# the bot has, so the bot makes them (as 0569 does for "Let me choose") and keeps one set for each
# DM in `web_reveals`: names and the statement in words (the same ones the card shows the DM), so
# the table holds campaign words, with forced row-level security, key `ON UPDATE CASCADE`, not in a
# backup, swept after an hour by the bot, and a read door that answers only a DM of the campaign.
# `reveals-ask` (no text) replaces the DM's set (so asking again never adds a second row for a
# proposal); `reveals-keep` / `reveals-not` carry the proposal's id (32 hex). The queue door's
# lists, shape check and the table's action list are read back as they are and each anchor must be
# found exactly once; 0583's last entries are the tail they extend.
ACTIONS = ("reveals-ask", "reveals-keep", "reveals-not")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose', 'leave-yes', 'leave-no', "
    "'thread-state', 'thread-check', 'thread-involve', 'thread-new', 'thread-due', 'thread-party', "
    "'thread-link', 'thread-led-to'"
)
_NEXT = _PREV + ", 'reveals-ask', 'reveals-keep', 'reveals-not'"

_ID = "[0-9a-f]{32}"
_LAST_SHAPE = f"           OR (act = 'thread-led-to' AND clean !~ '^{_ID} {_ID}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'reveals-ask' AND clean <> '')\n"
        + f"           OR (act IN ('reveals-keep', 'reveals-not') AND clean !~ '^{_ID}$')\n",
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
    CREATE TABLE web_reveals (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        proposal_id TEXT NOT NULL CHECK (proposal_id ~ '^[0-9a-f]{32}$'),
        position    INTEGER NOT NULL CHECK (position >= 0),
        who         TEXT NOT NULL CHECK (char_length(who) BETWEEN 1 AND 300),
        what        TEXT NOT NULL CHECK (char_length(what) BETWEEN 1 AND 600),
        how         TEXT NOT NULL CHECK (char_length(how) BETWEEN 1 AND 300),
        player      BOOLEAN NOT NULL,
        guessing    BOOLEAN NOT NULL,
        asked_at    BIGINT NOT NULL CHECK (asked_at > 0),
        PRIMARY KEY (guild_id, campaign_id, user_id, proposal_id)
    );
    CREATE INDEX web_reveals_age ON web_reveals (guild_id, asked_at);
    """
    + _isolate("web_reveals")
    + r"""
    CREATE FUNCTION dmbot_web_reveals(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', w.proposal_id, 'who', w.who, 'what', w.what, 'how', w.how,
                'player', w.player, 'guessing', w.guessing) ORDER BY w.position)
            FROM web_reveals w
            WHERE w.guild_id = g AND w.campaign_id = c AND w.user_id = dmbot_current_user()
        ), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_reveals(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_reveals(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("web_reveals",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_reveals", "dmbot_web_reveals(BIGINT, TEXT)"),
)
