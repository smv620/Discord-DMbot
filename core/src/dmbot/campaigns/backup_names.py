"""The names inside a backup, kept outside ui so the website's restore can use them (#2827)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from dmbot.discord_limits import OPTION_LABEL_MAX, shorten


def backup_filename(campaign_name: str, now: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.casefold()).strip("-")[:40] or "campaign"
    day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    return f"{slug}-{day}.dmbot.json.gz"  # compressed since #164; .dmbot.json still restores


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
