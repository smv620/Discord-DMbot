"""The uploader's own consent words (#3305; the owner's words are on that issue, and PLAN's
"The uploader's own words" once #3349 is in).

On the Import sessions page the person who ticks is not the person who is recorded, so the words
are addressed to the uploader and say what ticking means: what DMbot does with the text, who can
read it, which AI company reads it, the age rule, and how a person gets their lines removed. They
replace the Discord consent request there (which speaks to the person being recorded) and are
versioned apart from the Discord terms: `IMPORT_WORDS_VERSION` goes up when these words change,
and nobody's Discord consent is asked again. (Version 101 is the first wording; the next is 102.)

They are held back by a switch so that the last promise (a person can ask for their lines to be
removed) is true from the first day: `UPLOADER_WORDS_LIVE` is turned on in the same change as the
website's `SPEAKER_REMOVAL_LIVE` (web/src/content/speaker-removal-switch.ts; docs/GO_LIVE.md
notes it in the website switch's row: the web map lists website switches only).
Until then the page shows the Discord words as before, under the Discord terms version.

No age or birthdate is ever stored: the age rule is a fact the uploader affirms with the tick.
"""

from __future__ import annotations

from dmbot.consent import TERMS_VERSION

UPLOADER_WORDS_LIVE = False  # turned on together with the website's SPEAKER_REMOVAL_LIVE
# Its own number space, 100 and up: the Discord terms (TERMS_VERSION, and the held next one) and
# IMAGE_TERMS_VERSION count from 6, so a recorded 7 could mean any of three wordings. A record's
# `terms_version` is read as an uploader's words version when it is 100 or more.
IMPORT_WORDS_VERSION = 101

# (owner, 2026-11-03; the final age rule: 16 or older agrees for themselves, 13 to 15 need the
# uploader to check with a parent or guardian, nobody under 13)
TEXT = """\
**Before you save these sessions:** for each person you tick, you are confirming that they agreed to this.
- DMbot turns what they said, or wrote, in the file into text for this campaign.
- Anyone in this campaign's server can read and download that text.
- An AI company (Anthropic) reads the text, with who said it, to give the DM notes. It is not used to train their AI.
- Only tick for someone who is 16 or older and said yes. For someone who is 13 to 15, you must check with their parent or guardian, and tick only if the parent or guardian agreed. Never tick for anyone under 13.
- If they change their mind, their lines stay until they ask for them to be removed. They can ask at any time: on Discord with Delete my transcripts, or, if they are not on Discord, through the DM."""  # noqa: E501

# The tick, one for each person; `{name}` is the speaker as the page shows them.
TICK = (
    "I confirm {name} agreed to this, or, if they are 13 to 15, that I checked with their "
    "parent or guardian and they agreed."
)


def version() -> int:
    """The version of the words the page shows now, and the one a stale page is refused on."""
    return IMPORT_WORDS_VERSION if UPLOADER_WORDS_LIVE else TERMS_VERSION
