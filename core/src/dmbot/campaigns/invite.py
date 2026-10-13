"""A campaign's D&D Beyond invite link (#1119; docs/PLAN.md, "The D&D Beyond campaign invite").

DMbot can't create a D&D Beyond campaign or add anyone to one (there is no official API), so
the DM pastes their own campaign's invite link and DMbot passes it on to the players. Only a
link of exactly this shape is kept: `https://www.dndbeyond.com/campaigns/join/<code>`, with or
without `www.`. Anything else (another site, another D&D Beyond page, a look-alike address,
text around the link) is refused, because the link is put on a button players press.
"""

from __future__ import annotations

import re

from dmbot.campaigns.models import CampaignError

NOT_AN_INVITE = (
    "That isn't a D&D Beyond campaign invite link. On D&D Beyond open your campaign and copy "
    "its invite link: it starts https://www.dndbeyond.com/campaigns/join/"
)
PREFIX = "https://www.dndbeyond.com/campaigns/join/"
_INVITE = re.compile(
    r"https://(?:www\.)?dndbeyond\.com/campaigns/join/(?P<code>[A-Za-z0-9]{4,64})/?",
)


def parse_invite(text: str) -> str | None:
    """The invite link in its one standard form, or None for an empty box (which removes the
    link). Raises CampaignError with a plain line for anything that isn't an invite link."""
    text = text.strip()
    if not text:
        return None
    found = _INVITE.fullmatch(text)
    if found is None:
        raise CampaignError(NOT_AN_INVITE)
    return f"{PREFIX}{found['code']}"
