"""Why a recap's post was sent back to a draft (#3355): the sentence the DM reads, kept in
`recap_jobs.last_error`, and the code the website puts into words. A leaf module (no imports of the
recaps package), so the store, the bot and the page all read one list and can never drift apart."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

POST_NOTES: Final[Mapping[str, str]] = {
    "plan": (
        "Your plan doesn't include recaps right now, so nothing was posted. Your draft is saved."
    ),
    "no_table_chat": (
        "DMbot has no table chat to post in yet: start a session in a voice channel, or "
        "pick a chat channel in ⚙️ Manage DMbot. Then press Post."
    ),
    "cannot_check_secrets": "DMbot couldn't check your secret names just now. Try Post again.",
    "secret_name": "It names something you keep secret. Change the text, then post.",
    "player_stopped": (
        "A player stopped being recorded since this was written, so it can't be posted. "
        "Write a new recap, or leave this one out."
    ),
    "send_failed": (
        "DMbot couldn't write in the table's chat. Check it can post there, then press Post again."
    ),
    "post_failed": "Couldn't post it. Try Post again.",
}

# The page and Discord clear a refusal when the DM edits the text or presses Post again, so an old
# refusal never reads as a current failure. Only the sentences above are touched: the making half's
# own errors stay.
REFUSALS: Final[list[str]] = list(POST_NOTES.values())
CLEAR_REFUSAL: Final = "last_error = CASE WHEN last_error = ANY(%s) THEN '' ELSE last_error END"
