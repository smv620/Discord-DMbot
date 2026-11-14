"""The names inside a backup, kept outside ui so the website's restore can use them (#2827)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from dmbot import lineage
from dmbot.discord_limits import OPTION_LABEL_MAX, shorten

UNKNOWN_COMMIT = "unknown"
# The ending of a backup's file name, with or without the build's commit after "#" (#3391).
_ENDING = re.compile(r"\.dmbot(?:#[0-9a-z]+)?\.json\.gz$")


def build_commit() -> str:
    """The commit the running build was made from (the image gets it as GIT_COMMIT): the full
    40 characters, or "unknown" when the build has none. A backup says which build made it, so the
    data's lineage survives a renamed file (#3391)."""
    return lineage.running_commit() or UNKNOWN_COMMIT


def backup_filename(campaign_name: str, now: int, commit: str | None = None) -> str:
    """`slug-2026-11-04.dmbot#bd8ae13a.json.gz`: the first 8 characters of the commit after "#",
    `unknown` when the build has none. `commit` is for tests; the default is the running build's."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.casefold()).strip("-")[:40] or "campaign"
    day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    tag = (commit if commit is not None else build_commit())[:8] or UNKNOWN_COMMIT
    # (compressed since #164; a restore goes by the file's content, not by its name)
    return f"{slug}-{day}.dmbot#{tag}.json.gz"


def backup_stem(file_name: str) -> str:
    """A backup's file name without its ending, for naming the transcripts files beside it."""
    return _ENDING.sub("", file_name)


def backup_parts(data: object) -> int:
    """How many transcripts files a copy came with (#2552); 0 for one made before them."""
    manifest = data.get("parts") if isinstance(data, dict) else None
    count = manifest.get("count") if isinstance(manifest, dict) else None
    return count if type(count) is int and 0 < count <= 1000 else 0


def backup_campaign_name(data: object) -> str | None:
    """The campaign name inside a parsed backup, for messages; None if it's not there."""
    if isinstance(data, dict):
        campaign = data.get("campaign")
        if isinstance(campaign, dict):
            name = campaign.get("name")
            if isinstance(name, str) and name.strip():
                return shorten(name, OPTION_LABEL_MAX)
    return None
