"""Shortening at a word end, for a limit of storage or a service (#2597, #2601).

A length is a target in an AI's instructions and never a reason to refuse or drop what it wrote:
where a hard limit exists the text is shortened here, in code, and kept.
"""

from __future__ import annotations


def at_word_end(text: str, limit: int) -> str:
    """`text` shortened to at most `limit` characters at a word end, for a hard limit of a service
    or of storage; the text itself when it fits. Never a reason to ask the AI again (#2597)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit].isspace():
        return cut.rstrip()
    for at in range(len(cut) - 1, 0, -1):  # the last space or line break before the limit
        if cut[at].isspace():
            return cut[:at].rstrip()
    return cut  # one long word: cut where it is
