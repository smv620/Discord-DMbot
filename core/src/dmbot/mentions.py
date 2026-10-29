"""The one "ping nobody" setting every send uses.

DMbot posts campaign data (names, notes, transcript lines) as typed, so an `@everyone` or a role
mention in a name must never ping anyone. Nothing in DMbot is meant to ping (#1834).
"""

import discord

NO_PINGS = discord.AllowedMentions.none()
