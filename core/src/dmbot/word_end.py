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


def in_the_middle(text: str, limit: int, mark: str = " … ") -> str:
    """`text` shortened to at most `limit` characters by taking out the MIDDLE at word ends, so the
    start and the end both stay (a quote that reaches back for a name begins with the name and ends
    with the sentence that matters, #2757); the text itself when it fits. A third of the room goes
    to the front, the rest to the end."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    room = max(limit - len(mark), 2)
    head = at_word_end(text, max(room // 3, 1))
    tail_room = room - len(head)
    tail = text[-tail_room:]
    if tail_room < len(text) and not text[-tail_room - 1].isspace():
        # (cut inside a word: start the end at the next word)
        space = tail.find(" ")
        tail = tail[space + 1 :] if space >= 0 else tail
    return f"{head}{mark}{tail}".strip()
