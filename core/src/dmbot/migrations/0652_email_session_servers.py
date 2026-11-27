"""0652_email_session_servers

An email sign-in sees the servers of the person's last Discord sign-in (#5351).
"""

from __future__ import annotations

# The website's role sees a server only if it is in the session's own list (`dmbot_web_guilds`,
# 0016), and an email sign-in has no Discord token to ask for one, so its list was empty and every
# campaign page said "We couldn't find that campaign" until the person signed in with Discord. The
# account now remembers the list of its last Discord sign-in, and an email sign-in copies it into
# its session with `manage` turned off (adding DMbot to a server still needs a Discord sign-in).
# A list is only a ceiling: what a person sees inside a server is still the database's own rules for
# their campaigns. It must not outlive a Discord session, though: `last_guilds_at` is when it was
# taken, an email sign-in copies it only while it is younger than one session length, and caps its
# own expiry at that time plus the session length, so someone removed from a server cannot keep
# seeing it through repeated email sign-ins. `last_guilds_user` is the Discord account that took the
# list: an account move renames the row's `user_id` and keeps its other columns, and the old
# account's servers must not follow it to the new Discord account, so the list is used only while
# it was taken by the row's own `user_id`. Server ids and names only, as the session keeps.
SQL = """
    ALTER TABLE web_users ADD COLUMN last_guilds JSONB NOT NULL DEFAULT '[]'::jsonb;
    ALTER TABLE web_users ADD COLUMN last_guilds_at BIGINT;
    ALTER TABLE web_users ADD COLUMN last_guilds_user BIGINT;
"""
