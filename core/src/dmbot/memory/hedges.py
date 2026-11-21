"""When a sentence states nothing the set-phrase readers may keep (#4982): a question, a supposition
("If Hagmire Road is flooded, …"), or someone else's view ("Merchants say Crowmarsh is …"). The
place culture and road condition readers share these words, so a lore sentence is judged the same
way by both (the feelings reader keeps its own clause rule, `bias_read`). Pure. Saying nothing is
the safe side: a missing row costs the DM a line they can add, a wrong one costs them a 🚫."""

from __future__ import annotations

import re

# A sentence that asks or supposes ("Does X trust elves?", "If X is closed, …")
NOT_A_STATEMENT = re.compile(
    r"^\W*(?:if|does|do|did|should|will|would|could|can|unless|whether|perhaps|maybe|is|are|was"
    r"|were)\b",
    re.I,
)
# Someone else's view, said before the name in the same sentence
REPORTED = re.compile(
    r"\b(?:says?|said|claims?|claimed|believes?|believed|insists?|rumou?rs?|rumou?red|it is said"
    r"|they say|according to|swears?|swore)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"[.!?;\n]")


def sentence_at(text: str, start: int, end: int) -> tuple[str, str, str]:
    """(the sentence before the span, the span's sentence after it, the mark that ends it): the
    one sentence that holds text[start:end]."""
    begin = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    stop = _SENTENCE_END.search(text, end)
    finish = stop.start() if stop is not None else len(text)
    mark = stop.group() if stop is not None else ""
    return text[begin:start], text[end:finish], mark


def hedged(text: str, start: int, end: int) -> bool:
    """Whether the sentence holding text[start:end] states nothing a reader may keep: it asks
    ("?"), supposes (it starts with "if", "does", …), or reports someone's view before the span."""
    before, _after, mark = sentence_at(text, start, end)
    return mark == "?" or bool(NOT_A_STATEMENT.search(before)) or bool(REPORTED.search(before))


__all__ = ["NOT_A_STATEMENT", "REPORTED", "hedged", "sentence_at"]
