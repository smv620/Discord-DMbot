"""0256_plot_chat_followups

Hour_full, Save stamped, timed out (#2545).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    PLOT_CHAT,
)


def _plot_chat_followups() -> str:
    """The three talk doors of 0211, re-made with small changes (#2545), cut out of the text that
    made them so nothing else about them can drift: the campaign's hourly cap answers its own
    code (`hour_full`, so the page can say it is the hour and not the issue that is full); a Save
    is stamped with its time (`saving_at`); and a proposal still `saving` thirty minutes after
    that (or after its message, for one saved before this) is shown as failed when the DM next
    looks, so a Save made while PlotBot is off does not wait forever. The two give-ups (a message
    never answered, a Save never made) write a plain sentence as their error, not a code: the
    page prints errors word for word."""
    start = PLOT_CHAT.index("    CREATE FUNCTION dmbot_plot_chat_send")
    end = PLOT_CHAT.index("    DO $do$")
    doors = PLOT_CHAT[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    now = "floor(extract(epoch FROM clock_timestamp()))::BIGINT"
    edits = (
        (
            "        UPDATE plot_chat SET state = 'failed', error = 'timed_out'\n",
            "        UPDATE plot_chat SET state = 'failed',\n"
            "                             error = 'PlotBot didn''t answer. Ask it again.'\n",
        ),
        (
            "              AND m.created_at > now_s - 3600) >= 30 THEN\n"
            "            RETURN jsonb_build_object('status', 'too_many');",
            "              AND m.created_at > now_s - 3600) >= 30 THEN\n"
            "            RETURN jsonb_build_object('status', 'hour_full');",
        ),
        (
            "THEN jsonb_set(e, '{state}', to_jsonb(want)) ELSE e END",
            "THEN jsonb_set(e, '{state}', to_jsonb(want)) || CASE WHEN want = 'saving'\n"
            f"                THEN jsonb_build_object('saving_at', {now})\n"
            "                ELSE '{}'::jsonb END ELSE e END",
        ),
        (
            "        RETURN jsonb_build_object('messages', COALESCE((",
            "        -- A Save the bot has not made in thirty minutes (it was off) is given up.\n"
            "        UPDATE plot_chat SET proposals = (\n"
            "            SELECT jsonb_agg(CASE WHEN e->>'state' = 'saving'\n"
            "                    AND COALESCE((e->>'saving_at')::BIGINT, created_at)\n"
            f"                        < {now} - 1800\n"
            "                THEN e || jsonb_build_object('state', 'failed', 'error',\n"
            "                    'PlotBot didn''t save this in time. Ask it again.')\n"
            "                ELSE e END ORDER BY ord)\n"
            "            FROM jsonb_array_elements(proposals) WITH ORDINALITY AS t(e, ord))\n"
            "            WHERE guild_id = g AND campaign_id = c AND issue_id = p_issue\n"
            "              AND session_at = at_ AND user_id = dmbot_current_user()\n"
            "              AND role = 'bot'\n"
            "              AND EXISTS (SELECT 1 FROM jsonb_array_elements(proposals) x\n"
            "                  WHERE x->>'state' = 'saving'\n"
            "                    AND COALESCE((x->>'saving_at')::BIGINT, created_at)\n"
            f"                        < {now} - 1800);\n"
            "        RETURN jsonb_build_object('messages', COALESCE((",
        ),
    )
    for old, new in edits:
        if doors.count(old) != 1:  # (0211's text moved)
            raise RuntimeError("the plot chat doors changed: the follow-ups need a new anchor")
        doors = doors.replace(old, new, 1)
    # Replacing a function drops its pinned search path, so the pin is made again (0211's block).
    return (
        doors + PLOT_CHAT[end : PLOT_CHAT.index("    REVOKE ALL ON FUNCTION dmbot_plot_chat_send")]
    )


PLOT_CHAT_FOLLOWUPS = _plot_chat_followups()

SQL = PLOT_CHAT_FOLLOWUPS
