"""0193_link_pin_action

The website pins a connection (#2447).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _pin_door__3,
)

LINK_PIN_ACTION = """
    -- The website's 📌 pin (#2447): a DM pins a connection as 100% truth, or takes it back, through
    -- the same queue as the other connection edits ('link-pin': `target_id` the connection, `flag`
    -- pinned or not). The bot applies it with the call the name card uses, so the change log and
    -- Undo agree. No row changes: a wider list of actions and the door that checks it.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin'));
"""

LINK_PIN_ACTION = (
    LINK_PIN_ACTION
    + _pin_door__3
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""
)

SQL = LINK_PIN_ACTION
