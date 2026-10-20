"""Reading a transcript from a Google Doc share link (#2140), for the website's import.

Only Google Docs, only its plain-text export, only a doc shared as "Anyone with the link": the
guarded reader (`dmbot.fetch`) opens it with no cookie, token or Google account, follows
redirects only inside Google's own hosts, and stops at 2 MB or 30 seconds. The text is returned
for the caller to hold like an uploaded file's text; nothing is stored or logged here (the link
itself can be private, so it is never in a log line).
"""

from __future__ import annotations

from dmbot import fetch

MAX_BYTES = 2 * 1024 * 1024  # the same ceiling as an uploaded file
STAY_ON = ("docs.google.com", "accounts.google.com", "googleusercontent.com")

NOT_A_GOOGLE_DOC = "not_a_google_doc"
NOT_SHARED = "doc_not_shared"
TOO_BIG = "doc_too_big"
UNREACHABLE = "doc_unreachable"
REPEATED_LINK = "repeated_link"  # the same doc was pasted twice; only the first is read


class DocError(Exception):
    """Why a doc could not be read, as the code the API returns."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def read_doc(link: object) -> str:
    """The doc's text, or a DocError with one of the four codes."""
    if not isinstance(link, str) or len(link) > 2000 or fetch.google_doc_export(link) is None:
        raise DocError(NOT_A_GOOGLE_DOC)
    try:
        got = await fetch.fetch(link, max_bytes=MAX_BYTES, stay_on=STAY_ON)
    except fetch.NotShared:
        raise DocError(NOT_SHARED) from None
    except fetch.TooBig:
        raise DocError(TOO_BIG) from None
    except fetch.LinkError:
        raise DocError(UNREACHABLE) from None
    if got.filename != "link.txt":  # the export is plain text; anything else is not a doc
        raise DocError(UNREACHABLE)
    try:
        return got.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocError(UNREACHABLE) from None
