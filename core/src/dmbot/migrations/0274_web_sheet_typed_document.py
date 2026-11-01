"""0274_web_sheet_typed_document

Typed and document (#2818).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_SHEET_ACTIONS,
    _swap,
)

# The website's character sheets take two more choices (#2818): `typed` (the form Discord's "Type in
# my character" has: class, level, species and names, kept as one small JSON text in `payload`) and
# `document` (a public https web page or PDF link in `url`, which the bot fetches and reads; the
# website never does, and the bot's fetch is https only, so an http link is refused here too). The
# door gets the same fifth argument as for a link (`lnk`): the link for `document`, the JSON text
# for `typed`. It refuses a shape the bot would not read (`bad_document`, `bad_typed`) and the bot
# checks the rest again. A document read costs an AI call, so a person may queue one a minute,
# where a link or typed sheet may be queued every 20 seconds. The link stays in this row only; no
# door gives it back (the answer door returns the state and the plain error word). The door is cut
# from its text in 0268 by guarded replacement, so a drift fails loudly.
_SHEET_DOOR = WEB_SHEET_ACTIONS[
    WEB_SHEET_ACTIONS.index("    CREATE FUNCTION dmbot_web_sheet_action_add(") :
]

_SHEET_DOOR = _SHEET_DOOR[
    : _SHEET_DOOR.index("    -- The state of one of the caller's own choices")
]

_SHEET_DOOR = _swap(
    _SHEET_DOOR.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
    (
        (
            "        who TEXT := COALESCE(ent, '');\n    BEGIN",
            "        who TEXT := COALESCE(ent, '');\n"
            "        body TEXT := '';\n"
            "        pj JSONB;\n"
            "    BEGIN",
        ),
        (
            "IF act IS NULL OR act NOT IN ('link', 'forget', 'read_again') THEN",
            "IF act IS NULL\n"
            "           OR act NOT IN ('link', 'forget', 'read_again', 'typed', 'document') THEN",
        ),
        (
            "            IF act = 'forget' THEN\n                want := '';",
            "            IF act = 'document'\n"
            "               AND (char_length(want) > 400\n"
            "                    OR want !~ '^https://[^[:space:][:cntrl:]<>\"]+$') THEN\n"
            "                RETURN jsonb_build_object('status', 'bad_document');\n"
            "            END IF;\n"
            "            IF act = 'typed' THEN\n"
            "                -- the form as one JSON object with exactly its four fields, each in\n"
            "                -- the limits Discord's form has "
            "(class and species 60, level 1 to 20,\n"
            "                -- names 1500); the bot checks the words again\n"
            "                body := want;\n"
            "                want := '';\n"
            "                BEGIN\n"
            "                    pj := body::jsonb;\n"
            "                EXCEPTION WHEN others THEN\n"
            "                    RETURN jsonb_build_object('status', 'bad_typed');\n"
            "                END;\n"
            "                IF char_length(body) > 4000 OR jsonb_typeof(pj) <> 'object'\n"
            "                   OR (SELECT count(*) FROM jsonb_object_keys(pj)) <> 4\n"
            "                   OR NOT (pj ?& ARRAY['className', 'level', 'species', 'names'])\n"
            "                   OR jsonb_typeof(pj->'className') <> 'string'\n"
            "                   OR char_length(btrim(pj->>'className')) NOT BETWEEN 1 AND 60\n"
            "                   OR jsonb_typeof(pj->'level') <> 'number'\n"
            "                   OR (pj->>'level') !~ '^([1-9]|1[0-9]|20)$'\n"
            "                   OR jsonb_typeof(pj->'species') <> 'string'\n"
            "                   OR char_length(pj->>'species') > 60\n"
            "                   OR jsonb_typeof(pj->'names') <> 'string'\n"
            "                   OR char_length(pj->>'names') > 1500 THEN\n"
            "                    RETURN jsonb_build_object('status', 'bad_typed');\n"
            "                END IF;\n"
            "            END IF;\n"
            "            IF act = 'forget' THEN\n                want := '';",
        ),
        (
            "AND a.entity_id = who AND a.action = act AND a.url = want\n",
            "AND a.entity_id = who AND a.action = act AND a.url = want\n"
            "              AND a.payload = body\n",
        ),
        (
            "        IF act = 'link' AND EXISTS (SELECT 1 FROM web_sheet_actions a\n"
            "            WHERE a.guild_id = g AND a.campaign_id = c "
            "AND a.user_id = dmbot_current_user()\n"
            "              AND a.action = 'link' AND a.created_at > now_s - 20) THEN\n"
            "            RETURN jsonb_build_object('status', 'too_soon');\n"
            "        END IF;\n",
            "        IF act IN ('link', 'typed') AND EXISTS (SELECT 1 FROM web_sheet_actions a\n"
            "            WHERE a.guild_id = g AND a.campaign_id = c "
            "AND a.user_id = dmbot_current_user()\n"
            "              AND a.action IN ('link', 'typed') AND a.created_at > now_s - 20) THEN\n"
            "            RETURN jsonb_build_object('status', 'too_soon');\n"
            "        END IF;\n"
            "        IF act = 'document' AND EXISTS (SELECT 1 FROM web_sheet_actions a\n"
            "            WHERE a.guild_id = g AND a.campaign_id = c "
            "AND a.user_id = dmbot_current_user()\n"
            "              AND a.action = 'document' AND a.created_at > now_s - 60) THEN\n"
            "            RETURN jsonb_build_object('status', 'too_soon');\n"
            "        END IF;\n",
        ),
        (
            "INSERT INTO web_sheet_actions (guild_id, campaign_id, "
            "id, user_id, entity_id, action, url,\n"
            "                                       created_at, updated_at)\n"
            "            VALUES (g, c, rid, dmbot_current_user(), who, act, want, now_s, now_s);",
            "INSERT INTO web_sheet_actions (guild_id, campaign_id, "
            "id, user_id, entity_id, action, url,\n"
            "                                       payload, created_at, updated_at)\n"
            "            VALUES (g, c, rid, dmbot_current_user(), "
            "who, act, want, body, now_s, now_s);",
        ),
    ),
)

WEB_SHEET_TYPED_DOCUMENT = (
    r"""
    ALTER TABLE web_sheet_actions DROP CONSTRAINT web_sheet_actions_action_check;
    ALTER TABLE web_sheet_actions ADD CONSTRAINT web_sheet_actions_action_check
        CHECK (action IN ('link', 'forget', 'read_again', 'typed', 'document'));
    -- A document link is longer than a character link (Discord's field holds 400).
    ALTER TABLE web_sheet_actions DROP CONSTRAINT web_sheet_actions_url_check;
    ALTER TABLE web_sheet_actions ADD CONSTRAINT web_sheet_actions_url_check
        CHECK (char_length(url) <= 400);
    -- The typed form as JSON text (class, level, species, names); empty for every other choice.
    ALTER TABLE web_sheet_actions
        ADD COLUMN payload TEXT NOT NULL DEFAULT '' CHECK (char_length(payload) <= 4000);
    -- While a choice waits, the columns agree with it: only a typed sheet has a payload (so a link
    -- or a document can never carry stray text), and only a document or a link has an address. A
    -- finished row (done or failed) may be blanked by the bot, which keeps no link or words.
    ALTER TABLE web_sheet_actions ADD CONSTRAINT web_sheet_actions_shape_check CHECK (
        state IN ('done', 'failed')
        OR (((action = 'typed') = (payload <> ''))
            AND ((action IN ('link', 'document')) = (url <> ''))));
    COMMENT ON TABLE web_sheet_actions IS
        'A website choice about a character sheet waiting for the bot: link, forget, read_again, '
        'typed (payload: the form as JSON) or document (url: a public https link). Rows hold the '
        'link or the typed words only until the bot finishes the row.';
"""
    + _SHEET_DOOR
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_SHEET_TYPED_DOCUMENT
