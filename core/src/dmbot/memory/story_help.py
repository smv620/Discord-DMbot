"""When someone asks DMbot to invent or build the story (#2827): the plain no, and the check for
the ask, shared by recap notes and the plot chat.
"""

import re

STORY_HELP_REPLY = "DMbot only retells what happened at your table. The story is yours to write."

# A note that asks DMbot to make things up is the same as asking it to build the story. Matched on
# the note's words as well as the model's kind, because a model can be talked into calling it style.
_INVENT = re.compile(
    r"\b((?:make|makes|making)\s+(?:things|stuff|it|them|some\s+\w+)\s+up|"
    r"(?:make|makes|making)\s+up\b|made[- ]up|"
    r"invent\w*|fabricat\w*|embellish\w*|exaggerat\w*|dramati[sz]\w*|"
    # "add" asks for the new only when the thing added is new or the DM's own: "add more details
    # about the fight" asks for what happened, which is fine.
    r"add\w*\s+(?:\w+\s+)?(?:drama|twists?)|"
    r"add\w*\s+(?:an?\s+|some\s+|a\s+few\s+)?(?:new|your\s+own|made-up|extra|fake)\s+\w+|"
    r"(?:fill|filling)\s+in\s+the\s+gaps|"
    r"(?:ignore|break|skip|bend|loosen)\w*\s+(?:the\s+|any\s+)?(?:rules?|checks?)|"
    r"(?:use|allow)\w*\s+(?:any|other|new)\s+names)\b",
    re.IGNORECASE,
)
# "Never make things up" asks for the opposite: a standing note that agrees with the rules. The
# rule leans to refusing (over-refusing costs the DM a rephrase; under-refusing breaks the content
# rule): an invent word is accepted only when ONE negation stands directly in front of it, with at
# most one word between ("Never make things up", "Avoid inventing details"), and no second
# negation or reversal ("stop", "forget", "fail", "quit", "neglect") anywhere before it. Anything
# longer ("Avoid dullness by inventing twists", "Don't stop making things up") is refused.
_NEGATION = (
    r"never|don['’]?t|do\s+not|doesn['’]?t|stop|without|avoid|no\s+more|not"
    r"|forget|fail|quit|neglect"
)
_NEGATORS = re.compile(rf"\b(?:{_NEGATION})\b", re.IGNORECASE)
_DIRECT = re.compile(
    r"\b(?:never|don['’]?t|do\s+not|doesn['’]?t|stop|without|avoid|no\s+more|not)"
    r"(?:[ \t]+[\w'’]+)?[ \t]*$",
    re.IGNORECASE,
)


# One filler word in between turns the ban into something else ("Never mind, invent twists").
_FILLER = re.compile(r"\b(?:mind|worry|hesitate|be|fear)\s*$", re.IGNORECASE)


def _negated(before: str) -> bool:
    return (
        len(_NEGATORS.findall(before)) == 1
        and _DIRECT.search(before) is not None
        and _FILLER.search(before) is None
    )


def asks_to_invent(text: str) -> bool:
    """Does this note ask DMbot to make things up or loosen a rule? A note that forbids it
    ("never make things up") does not."""
    return any(not _negated(text[: m.start()]) for m in _INVENT.finditer(text))
