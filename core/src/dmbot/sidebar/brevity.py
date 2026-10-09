"""How short a sidebar answer must be (owner, 2026-10-09: short answers matter most; #934).

The prompt asks for brevity, but the rules also live here, in code, because a model slips:
an answer is at most `MAX_SENTENCES` sentences and `MAX_CHARS` characters (the short source
and "sure" come after, outside the limit) unless the DM asked for the full text. A yes-or-no
question gets "Yes." or "No." first. Greetings, repeated questions and offers of more help
are removed. All of it is pure text work, so it tests with no AI and no Discord.
"""

from __future__ import annotations

import re

MAX_SENTENCES = 2
MAX_CHARS = 200

# Words in the DM's question that mean "give me the whole thing".
_FULL_TEXT = re.compile(
    r"\b((spell|rule|condition|monster|creature|full|complete|entire|whole) description|"
    r"description of (the |a |an )?(spell|rule|condition|monster|creature)|"
    r"full text|text of (the )?(spell|rule|condition)|"
    r"(whole|entire) (spell|rule|text|thing|entry)|read (me )?(out )?(the )?(whole|full|entire)|"
    r"word for word|exact (text|wording)|stat ?block|complete (text|entry))\b",
    re.IGNORECASE,
)
# A question that a plain yes or no answers: "do you need…", "can a…", "if you need…".
_YES_NO = re.compile(
    r"^\s*(?:(?:hold on|wait|quick|so|ok|okay|um|uh)[,\s]+)*"
    r"(?:(?:i )?(?:need to |want to )?(?:find|check|look up|know) )?"
    r"(?:(?:if|whether)\b|(?:do|does|did|can|could|is|are|was|were|will|would|should|has|have|"
    r"may|must|need|am)\b)",
    re.IGNORECASE,
)
_ANSWERS_FIRST = re.compile(
    r"^\s*(yes|no|not sure|the free rules don't say|i don't have|your call)\b", re.IGNORECASE
)

# Padding to strip: whole sentences that only fill space.
_PAD_SENTENCE = re.compile(
    r"\b(let me know|hope (this|that) helps|feel free|(happy|glad) to help|"
    r"is there anything else|would you like( me)?( to)?|"
    r"if you (need|want|would like) (me to|more detail|further|to know more)|"
    r"i can (also )?(explain|elaborate|read|give you))\b",
    re.IGNORECASE,
)
_PAD_OPENER = re.compile(
    r"^\s*((great|good|excellent|fair|nice|interesting) (question|point)|sure thing)"
    r"\s*[!.,:\-–—]*\s*",
    re.IGNORECASE,
)
# "Sure, it can." says yes: keep the yes.
_YES_OPENER = re.compile(
    r"^\s*(sure|certainly|of course|absolutely)\s*[,!]\s*(?=[a-z])", re.IGNORECASE
)
_BARE_OPENER = re.compile(r"^\s*(sure|certainly|of course|absolutely)\s*[,!]\s*", re.IGNORECASE)
# Words whose dot does not end a sentence. Matched as the whole last word of a piece, so "stop."
# and "map." still end one. ("No." is handled apart: at the start of an answer it is the answer;
# only "No. 5" is an abbreviation.)
_ABBREVIATIONS = frozenset({"p.", "pp.", "e.g.", "i.e.", "vs.", "etc.", "cf.", "approx.", "lvl."})


def asks_for_full_text(question: str) -> bool:
    """The DM asked for the whole text ("the spell description", "read me the whole rule")."""
    return bool(_FULL_TEXT.search(question))


def is_yes_no_question(question: str) -> bool:
    """A question a plain yes or no answers. A choice ("advantage or disadvantage?") is not."""
    return bool(_YES_NO.match(question)) and not re.search(r"\bor\b", question, re.IGNORECASE)


def starts_with_the_answer(text: str) -> bool:
    """A yes-or-no question's answer starts with Yes/No, or says honestly it can't say."""
    return bool(_ANSWERS_FIRST.match(text))


def sentences(text: str) -> list[str]:
    """The text as sentences. A dot after "p." or "e.g." or inside "5.2.1" does not end one."""
    text = " ".join(text.split())
    if not text:
        return []
    out: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]+[\"')\]]*\s+(?=[A-Z0-9\"'(\[])", text):
        piece = text[start : match.start() + len(match.group().rstrip())]
        last = piece.split()[-1].lower() if piece.split() else ""
        if last in _ABBREVIATIONS or (last == "no." and text[match.end()].isdigit()):
            continue
        out.append(piece.strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def strip_padding(text: str) -> str:
    """Drop a greeting and sentences that only offer more or fill space."""
    text = _PAD_OPENER.sub("", " ".join(text.split()), count=1)
    text = _YES_OPENER.sub("Yes, ", text, count=1)
    text = _BARE_OPENER.sub("", text, count=1)  # "Sure, 8d6 fire damage." (no yes in it)
    kept = [s for s in sentences(text) if not _PAD_SENTENCE.search(s)]
    return " ".join(kept).strip()


def violations(question: str, text: str, *, full_text: bool) -> list[str]:
    """What is wrong with an answer, in words fit to tell the model, or [] if it is fine."""
    if full_text:
        return []
    problems: list[str] = []
    parts = sentences(text)
    if len(parts) > MAX_SENTENCES:
        problems.append(f"use at most {MAX_SENTENCES} sentences")
    if len(text) > MAX_CHARS:
        problems.append(f"use at most {MAX_CHARS} characters")
    if is_yes_no_question(question) and not starts_with_the_answer(text):
        problems.append('start with "Yes." or "No."')
    if text != strip_padding(text):
        problems.append("leave out greetings and offers of more help")
    return problems


def shorten(text: str) -> str:
    """Cut an answer to the limit without sending a paragraph: the first sentences that fit,
    else the first sentence trimmed at a word. Never longer than `MAX_CHARS`."""
    kept: list[str] = []
    for part in sentences(strip_padding(text))[:MAX_SENTENCES]:
        candidate = " ".join([*kept, part])
        if len(candidate) > MAX_CHARS:
            break
        kept.append(part)
    if kept:
        return " ".join(kept)
    first = (sentences(strip_padding(text)) or [text.strip()])[0]
    if len(first) <= MAX_CHARS:
        return first
    cut = first[: MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def within_limit(text: str) -> bool:
    return len(sentences(text)) <= MAX_SENTENCES and len(text) <= MAX_CHARS


def join_source(text: str, source: str | None, sure: str | None) -> str:
    """The answer with its source and confidence in a few words: `(SRD 5.2.1 p. 131, sure)`.
    Not repeated when the answer already says it: a source named in the text is left out, and
    "not sure" is left out of an answer that already says the rules don't say. The limit on
    length is for the answer itself; this suffix comes after it (so a message can be about 35
    characters over 200, which is fine on a phone)."""
    if source and source.lower() in text.lower():
        source = None
    if (
        sure == "not sure"
        and starts_with_the_answer(text)
        and not re.match(r"\s*(yes|no)\b", text, re.I)
    ):
        sure = None
    elif not source and sure == "sure":
        sure = None  # "(sure)" alone says nothing
    pieces = [p for p in (source, sure) if p]
    return f"{text} ({', '.join(pieces)})" if pieces else text
