"""A campaign's D&D Beyond invite link (#1119; docs/PLAN.md, "The D&D Beyond campaign invite").

DMbot can't create a D&D Beyond campaign or add anyone to one (there is no official API), so
the DM pastes their own campaign's invite link and DMbot passes it on to the players. Only a
link of exactly the shape `https://www.dndbeyond.com/campaigns/join/<code>` is kept (with or
without `www.`). Anything else (another site, another D&D Beyond page, a look-alike address,
text around the link) is refused, because the link is put on a button players press. If a real
invite turns out to have another shape, adding it is a one-line change here and in the
database CHECK (migration 0051).
"""

from __future__ import annotations

import re

from dmbot.campaigns.models import CampaignError

NOT_AN_INVITE = (
    "That isn't a D&D Beyond invite link. Open your campaign on D&D Beyond, copy its invite "
    "link, and paste only the link. It starts with https://www.dndbeyond.com/campaigns/join/"
)
_INVITE = re.compile(
    r"https://(?:www\.)?dndbeyond\.com/campaigns/join/"
    r"(?P<code>[A-Za-z0-9]{4,64})/?",
)


def parse_invite(text: str) -> str | None:
    """The invite link in its standard form (`www`, no trailing slash, the path as given), or
    None for an empty box (which removes the link). Raises CampaignError with a plain line for
    anything that isn't an invite link."""
    text = text.strip()
    if not text:
        return None
    found = _INVITE.fullmatch(text)
    if found is None:
        raise CampaignError(NOT_AN_INVITE)
    return f"https://www.dndbeyond.com/campaigns/join/{found['code']}"
