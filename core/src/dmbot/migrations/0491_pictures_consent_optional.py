"""0491_pictures_consent_optional

A kept picture records the poster's own yes to their table using it (`consent_version`,
`right_granted_at`) only when it was captured from the table's chat, which needs that yes. A map
the person sends to DMbot themselves (`/dmbot map`, a private chat, the website; #4372) is their
own act with its own right-to-use answer on the map, so no chat-capture consent exists, and
stamping one would be a false record (Supervisor, #4408). Both columns may now be NULL, together,
and only for a picture that did not come from the chat (`message_id` 0); a chat picture always
carries them. No row changes (every existing row keeps its values), so no row-level security needs
opening.
"""

from __future__ import annotations

SQL = r"""
    ALTER TABLE campaign_pictures
        ALTER COLUMN consent_version DROP NOT NULL,
        ALTER COLUMN right_granted_at DROP NOT NULL;
    -- (the older checks, >= 7 and > 0, already pass a NULL)
    ALTER TABLE campaign_pictures
        ADD CONSTRAINT campaign_pictures_consent_pair CHECK (
            (consent_version IS NULL) = (right_granted_at IS NULL)
            AND (consent_version IS NOT NULL OR message_id = 0)
        );
"""
