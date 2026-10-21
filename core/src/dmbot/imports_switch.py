"""Which servers may import older sessions (#2259).

Imports are off for everyone (`DMBOT_IMPORTS=0`) until dev1 has checked a real import. This adds a
way to turn them on for chosen servers only (`DMBOT_IMPORTS_SERVERS`, a list of server ids), so the
owner can try them on a test server first. The bot and the web API read the same two settings and
ask the same question here, so they never disagree. The ids are never logged or shown.
"""

from __future__ import annotations


def parse_servers(raw: str) -> frozenset[int]:
    """`DMBOT_IMPORTS_SERVERS`: Discord server ids, comma-separated, empty by default. Raises
    ValueError naming the setting and never an id."""
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(p.isascii() and p.isdigit() and 0 < int(p) < 2**63 for p in ids):
        raise ValueError(
            "DMBOT_IMPORTS_SERVERS must be Discord server numbers (digits only, up to 19), "
            "separated by commas. Fix it in .env and start again."
        )
    return frozenset(int(p) for p in ids)


def enabled(everywhere: bool, servers: frozenset[int], guild_id: int) -> bool:
    """Imports are on for a server when they are on for all (`DMBOT_IMPORTS=1`) or it is listed."""
    return everywhere or guild_id in servers
