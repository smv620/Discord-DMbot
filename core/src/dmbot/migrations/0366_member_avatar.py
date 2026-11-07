"""0366_member_avatar

A player's Discord avatar, for the Story Graph's player character icons (#3621).
"""

from __future__ import annotations

# The avatar's hash (never the image), kept beside the names in the member directory. That table
# keeps only members who agreed to be recorded and drops a row when they stop, so a player who has
# not agreed, has stopped or has left has no avatar here. The URL is built when it is read
# (`cdn.discordapp.com/avatars/<user>/<hash>.png`), so only a hash of 32 hex digits, with Discord's
# `a_` prefix for an animated one, is ever kept: nothing a user typed.
SQL = r"""
    ALTER TABLE member_directory
        ADD COLUMN avatar TEXT CHECK (avatar IS NULL OR avatar ~ '^(a_)?[0-9a-f]{32}$');
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
