"""0014_installs_left_at."""

from __future__ import annotations

INSTALLS_LEFT = """
    -- When DMbot left the server (kicked or the server deleted); NULL while it's there.
    -- Rows are never deleted on leave, so who added DMbot is kept if it comes back (#469).
    -- This replaces 0011's "only the bot removes a row": nobody removes one now.
    --
    -- `via` says how the installer was identified (#497): 'site' once Discord confirmed
    -- the install to the website for the signed-in person; 'link' for a plain join, kept
    -- when "Link this server" fills the installer in. The bot usually writes its 'link'
    -- row before the website's callback arrives, so 'site' overwrites it.
    --
    -- How each side writes a row (the contract, #469):
    -- - The bot, when it joins a server (Database.guild):
    --     INSERT ... VALUES (guild, NULL, now, 'link') ON CONFLICT (guild_id)
    --       DO UPDATE SET left_at = NULL
    --   and when it leaves: UPDATE installs SET left_at = now WHERE guild_id = guild.
    -- - The website, after Discord confirms an install (Database.user(install_guild=...)):
    --     INSERT ... VALUES (guild, me, now, 'site') ON CONFLICT (guild_id) DO UPDATE
    --       SET installed_by_user_id = EXCLUDED.installed_by_user_id, via = 'site',
    --           left_at = NULL
    --       WHERE installs.installed_by_user_id IS NULL
    --          OR installs.installed_by_user_id = EXCLUDED.installed_by_user_id
    --       RETURNING guild_id
    --   Someone else's install is skipped quietly (the WHERE is applied before the update
    --   policy; checked on Postgres 16): no row comes back, and the site says so.
    ALTER TABLE installs ADD COLUMN left_at BIGINT;
    COMMENT ON TABLE installs IS
        'Who added DMbot to which server. Never deleted on leave: left_at is set. '
        'Write contract: see migration 0014 in core/src/dmbot/schema.py.';
    """

SQL = INSTALLS_LEFT
